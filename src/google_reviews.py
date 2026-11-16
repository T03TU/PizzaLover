"""Bounded Google Places (New) review import into the existing review schema."""

import hashlib
import os

import httpx
from pydantic import ValidationError

from api.schemas import Review


class GoogleReviewsError(Exception):
    """An actionable provider error, safe to display without exposing credentials."""


async def fetch_google_reviews(country_code, region, max_places):
    """Fetch one bounded search page; country components enforce country selection.

    Region text scopes the search query; it is not an administrative boundary.
    Google supplies at most five relevance-ranked reviews per business.
    """
    key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    if not key:
        raise GoogleReviewsError("Set GOOGLE_PLACES_API_KEY on the server and enable Places API (New).")
    query = f"pizza restaurants in {region + ', ' if region else ''}{country_code}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://places.googleapis.com/v1/places:searchText",
                headers={"X-Goog-Api-Key": key,
                         "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.addressComponents,places.reviews"},
                json={"textQuery": query, "regionCode": country_code,
                      "includedType": "pizza_restaurant", "strictTypeFiltering": True,
                      "pageSize": max_places})
            if response.status_code != 200:
                messages = {400: "Google rejected the search. Check the country code and region.",
                            403: "Google denied access. Check the API key, billing, and Places API (New).",
                            429: "Google Places quota exceeded. Retry later or check your quota."}
                raise GoogleReviewsError(messages.get(response.status_code, "Google Places is unavailable. Retry later."))
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GoogleReviewsError("Could not read Google Places data. Retry later.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("places", []), list):
        raise GoogleReviewsError("Google returned an unexpected response.")
    reviews = {}
    places_count = 0
    skipped = 0
    for place in payload.get("places", [])[:max_places]:
        countries = [c.get("shortText", "").upper() for c in place.get("addressComponents", [])
                     if "country" in c.get("types", [])]
        if country_code not in countries:
            continue
        places_count += 1
        for row in place.get("reviews", [])[:5]:
            text = row.get("originalText", {}).get("text") or row.get("text", {}).get("text")
            # Review resource names are stable across translations and repeated imports.
            identity = row.get("name")
            if not identity and place.get("id") and row.get("publishTime"):
                identity = "|".join((place["id"], row.get("authorAttribution", {}).get("uri", ""), row["publishTime"]))
            if not text or not identity:
                skipped += 1
                continue
            try:
                review = Review(Feedback_ID="FB-G-" + hashlib.sha256(identity.encode()).hexdigest()[:40],
                                Franchise=place.get("displayName", {}).get("text", ""),
                                Location=place.get("formattedAddress", ""),
                                Rating=row.get("rating"), Comment=text)
            except ValidationError:
                skipped += 1
                continue
            reviews[review.Feedback_ID] = review
    return list(reviews.values()), places_count, skipped
