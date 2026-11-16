"""Google import contracts without network calls or persistent writes."""
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from api.schemas import Review
from src.google_reviews import GoogleReviewsError, fetch_google_reviews


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_mapping_country_and_stable_ids(self):
        place = {"id": "pizza1", "displayName": {"text": "Pizza shop"},
                 "formattedAddress": "Auckland, New Zealand",
                 "addressComponents": [{"types": ["country"], "shortText": "NZ"}],
                 "reviews": [{"name": "places/pizza1/reviews/one", "rating": 5,
                              "originalText": {"text": "Great pizza"}},
                             {"name": "empty", "rating": 4}]}
        foreign = dict(place, addressComponents=[{"types": ["country"], "shortText": "AU"}])
        response = httpx.Response(200, json={"places": [place, foreign]})
        with patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "test"}), patch('httpx.AsyncClient.post', new_callable=AsyncMock, return_value=response) as post:
            rows, places, skipped = await fetch_google_reviews('NZ', 'Auckland', 10)
            again, _, _ = await fetch_google_reviews('NZ', 'Auckland', 10)
        self.assertEqual((len(rows), places, skipped), (1, 1, 1))
        self.assertEqual(rows, again)
        self.assertEqual(rows[0].Franchise, 'Pizza shop')
        self.assertEqual(rows[0].Comment, 'Great pizza')
        self.assertEqual(post.call_args.kwargs['json']['includedType'], 'pizza_restaurant')

    async def test_missing_key_and_quota(self):
        with patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": ""}):
            with self.assertRaisesRegex(GoogleReviewsError, 'Set GOOGLE'):
                await fetch_google_reviews('NZ', '', 5)
        with patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "secret"}), patch('httpx.AsyncClient.post', new_callable=AsyncMock, return_value=httpx.Response(429)):
            with self.assertRaisesRegex(GoogleReviewsError, 'quota'):
                await fetch_google_reviews('NZ', '', 5)

    async def test_endpoint_repeat(self):
        import asyncio
        from types import SimpleNamespace
        from api.server import ingest_google
        from api.schemas import GoogleIngestRequest
        rows = []
        def add(docs, ids):
            rows.extend(doc.metadata for doc in docs)
        store = SimpleNamespace(get=lambda **kw: {"metadatas": rows}, add_documents=add)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            engine=SimpleNamespace(store=store), model_lock=asyncio.Lock())))
        review = Review(Feedback_ID='FB-G-123', Franchise='Pizza', Location='NZ', Rating=5, Comment='Great')
        async def inline(function, *args, **kwargs):
            return function(*args, **kwargs)
        with patch('src.google_reviews.fetch_google_reviews', new_callable=AsyncMock, return_value=([review], 1, 0)), patch('api.server.asyncio.to_thread', side_effect=inline):
            body = GoogleIngestRequest(country_code='NZ', region='Auckland', max_places=5)
            self.assertEqual((await ingest_google(body, request))['added'], 1)
            self.assertEqual((await ingest_google(body, request))['duplicates'], 1)
        self.assertEqual(len(rows), 1)
