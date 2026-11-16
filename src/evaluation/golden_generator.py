"""Generate, cache, and serialize reference cases for pizza RAG evaluation.

PERSONAS defines four reviewer roles used to condition synthesis.
SEED_SYNTHETIC_TESTSET contains six curated fallback cases. Case dictionaries
store user_input, reference, reference_contexts, synthesizer_type, persona,
franchise, and location. Fresh generation produces four cases with Ragas and
Ollama; missing caches use seeds unless regeneration is explicitly requested.
"""

import asyncio
import json
from pathlib import Path
from typing import List

import pandas as pd
from openai import OpenAI
from ragas.llms import llm_factory
from ragas.testset.persona import Persona
from ragas.testset.synthesizers.multi_hop.prompts import QueryConditions as MultiHopQueryConditions
from ragas.testset.synthesizers.multi_hop.specific import MultiHopSpecificQuerySynthesizer
from ragas.testset.synthesizers.single_hop.prompts import QueryCondition as SingleHopQueryCondition
from ragas.testset.synthesizers.single_hop.specific import SingleHopSpecificQuerySynthesizer

import config

PERSONAS = [
    Persona(
        name="Gourmet Food Critic",
        role_description="Interested in artisanal ingredients, flavor profiles, and authentic regional Italian pizza styles.",
    ),
    Persona(
        name="Budget-Conscious Student",
        role_description="Looking for fast delivery, large portion sizes, value deals, and late-night options.",
    ),
    Persona(
        name="Family Dinner Organizer",
        role_description="Evaluating consistent taste, friendly staff, cozy ambiance, and family-friendly dining experiences.",
    ),
    Persona(
        name="Neighborhood Regular",
        role_description="Interested in local branch service speed, waiting times, and Friday night customer experiences.",
    ),
]

SEED_SYNTHETIC_TESTSET = [
    {
        "user_input": "What is the truffle mushroom pizza like at Georgies Pizza in South Auckland?",
        "reference": "The truffle mushroom pizza at Georgies Pizza in South Auckland is rated 4 to 5 stars and described as 'out of this world', though customers note it can be a bit pricey but worth every cent.",
        "reference_contexts": [
            "The truffle mushroom pizza is out of this world.",
            "A bit pricey, but totally worth every cent.",
        ],
        "synthesizer_type": "single_hop_specific",
        "persona": "Gourmet Food Critic",
        "franchise": "Georgies Pizza",
        "location": "South Auckland",
    },
    {
        "user_input": "Which restaurant in North Shore offers authentic Italian pizza with fresh basil and mozzarella?",
        "reference": "Italianos on the North Shore offers pizza that 'tastes like Naples' with fresh basil and a perfect mozzarella balance, though it has slightly longer wait times.",
        "reference_contexts": [
            "Atmosphere is lovely, pizza tastes like Naples.",
            "Fresh basil and perfect mozzarella balance.",
            "Slightly longer wait times, but high quality.",
        ],
        "synthesizer_type": "single_hop_specific",
        "persona": "Gourmet Food Critic",
        "franchise": "Italianos",
        "location": "North Shore",
    },
    {
        "user_input": "Where can I find large portion sizes and authentic New York style pepperoni slices?",
        "reference": "Sal's in Westcity and Downtown is known for huge portions and the 'best pepperoni in town', featuring authentic New York style slices and piping hot quick service.",
        "reference_contexts": [
            "Huge portions, best pepperoni in town.",
            "Amazing authentic New York style slices!",
            "Quick service and piping hot pizza.",
        ],
        "synthesizer_type": "single_hop_specific",
        "persona": "Budget-Conscious Student",
        "franchise": "Sals",
        "location": "Downtown / Westcity",
    },
    {
        "user_input": "How does Dominoes compare in service speed and value for a quick family dinner?",
        "reference": "Dominoes in South Auckland and North Shore is praised for fast delivery, great value deals, and consistent taste suitable for quick family dinners, making it a good budget option when hungry.",
        "reference_contexts": [
            "Fast delivery and great value deals.",
            "Consistent taste, perfect for a quick family dinner.",
            "Good budget option when you are starving.",
        ],
        "synthesizer_type": "single_hop_specific",
        "persona": "Family Dinner Organizer",
        "franchise": "Dominoes",
        "location": "North Shore / South Auckland",
    },
    {
        "user_input": "Are there any service delays reported at PizzaHut on Friday nights?",
        "reference": "Yes, reviews for PizzaHut in South Auckland, East Auckland, and Downtown mention that service was a bit slow on Friday nights, although customers enjoy their stuffed crust and nostalgic pan pizza.",
        "reference_contexts": [
            "Service was a bit slow on a Friday night.",
            "Stuffed crust is a game changer.",
            "Nostalgic pan pizza, just like I remember.",
        ],
        "synthesizer_type": "single_hop_specific",
        "persona": "Neighborhood Regular",
        "franchise": "PizzaHut",
        "location": "Multiple Locations",
    },
    {
        "user_input": "Between Georgies Pizza and Italianos, how do their artisan quality and staff atmospheres compare across East Auckland and Westcity?",
        "reference": "Georgies Pizza in East Auckland is known for friendly staff and a cozy neighborhood vibe with fresh toppings, while Italianos in Westcity is highlighted for true Italian artisan quality, rich sauce, and high quality despite slightly longer wait times.",
        "reference_contexts": [
            "Friendly staff and cozy neighborhood vibe.",
            "Best local gourmet pizzas with fresh toppings!",
            "True Italian artisan quality and rich sauce.",
            "Slightly longer wait times, but high quality.",
        ],
        "synthesizer_type": "multi_hop_comparison",
        "persona": "Gourmet Food Critic",
        "franchise": "Georgies Pizza vs Italianos",
        "location": "East Auckland / Westcity",
    },
]


async def generate_synthetic_samples_from_reviews(
    csv_path: Path = config.RAW_DATA_PATH,
    ollama_base_url: str = config.OLLAMA_BASE_URL,
    model_name: str = config.LLM_MODEL,
) -> List[dict]:
    """Synthesize three single-hop cases and one cross-franchise comparison.

    Args:
        csv_path: Preferred review CSV; uses the legacy path when absent. Must
            contain Franchise and Comment columns.
        ollama_base_url: Ollama base URL; /v1 is appended for the OpenAI client.
        model_name: Ollama generation model used by the Ragas synthesizers.

    Returns:
        Four case dictionaries with user_input, reference, reference_contexts,
        synthesizer_type, persona, franchise, and location fields.

    Notes:
        gp_reviews, it_reviews, and sals_reviews take up to three comments per
        franchise. multi_context combines two Georgies and two Italianos comments.
        cond1 through cond4 specify persona, style, and evidence; res1 through
        res4 contain generated queries and answers. Calls Ollama and propagates
        CSV, schema, and synthesis errors for the caller to handle.
    """
    resolved_csv = csv_path if csv_path.exists() else config.LEGACY_RAW_DATA_PATH
    df = pd.read_csv(resolved_csv)

    client = OpenAI(base_url=f"{ollama_base_url}/v1", api_key="ollama")
    llm = llm_factory(model_name, client=client)

    single_synth = SingleHopSpecificQuerySynthesizer(llm=llm)
    multi_synth = MultiHopSpecificQuerySynthesizer(llm=llm)

    generated_samples = []

    print(f"Generating synthetic testset using Ragas Synthesizers & Ollama ({model_name})...")

    # 1. Single-hop: Georgies Pizza
    print("  -> Synthesizing testcase 1 (Single-Hop: Georgies Pizza)...")
    gp_reviews = df[df["Franchise"] == "Georgies Pizza"]["Comment"].tolist()[:3]
    cond1 = SingleHopQueryCondition(
        persona=PERSONAS[0],
        term="truffle mushroom pizza",
        query_style="Curious and detailed",
        query_length="Medium",
        context="\n".join(gp_reviews),
    )
    res1 = await single_synth.generate_query_reference_prompt.generate(llm, data=cond1)
    generated_samples.append({
        "user_input": res1.query,
        "reference": res1.answer,
        "reference_contexts": gp_reviews,
        "synthesizer_type": "single_hop_specific",
        "persona": PERSONAS[0].name,
        "franchise": "Georgies Pizza",
        "location": "South Auckland",
    })

    # 2. Single-hop: Italianos
    print("  -> Synthesizing testcase 2 (Single-Hop: Italianos)...")
    it_reviews = df[df["Franchise"] == "Italianos"]["Comment"].tolist()[:3]
    cond2 = SingleHopQueryCondition(
        persona=PERSONAS[0],
        term="fresh basil and mozzarella balance",
        query_style="Food critic review",
        query_length="Medium",
        context="\n".join(it_reviews),
    )
    res2 = await single_synth.generate_query_reference_prompt.generate(llm, data=cond2)
    generated_samples.append({
        "user_input": res2.query,
        "reference": res2.answer,
        "reference_contexts": it_reviews,
        "synthesizer_type": "single_hop_specific",
        "persona": PERSONAS[0].name,
        "franchise": "Italianos",
        "location": "North Shore",
    })

    # 3. Single-hop: Sal's
    print("  -> Synthesizing testcase 3 (Single-Hop: Sal's)...")
    sals_reviews = df[df["Franchise"] == "Sals"]["Comment"].tolist()[:3]
    cond3 = SingleHopQueryCondition(
        persona=PERSONAS[1],
        term="best pepperoni and huge portions",
        query_style="Casual student inquiry",
        query_length="Short",
        context="\n".join(sals_reviews),
    )
    res3 = await single_synth.generate_query_reference_prompt.generate(llm, data=cond3)
    generated_samples.append({
        "user_input": res3.query,
        "reference": res3.answer,
        "reference_contexts": sals_reviews,
        "synthesizer_type": "single_hop_specific",
        "persona": PERSONAS[1].name,
        "franchise": "Sals",
        "location": "Westcity / Downtown",
    })

    # 4. Multi-hop: Cross-Franchise Comparison
    print("  -> Synthesizing testcase 4 (Multi-Hop Comparison: Georgies vs Italianos)...")
    multi_context = gp_reviews[:2] + it_reviews[:2]
    cond4 = MultiHopQueryConditions(
        persona=PERSONAS[2],
        themes=["Artisan quality", "Staff friendliness"],
        query_style="Comparative inquiry",
        query_length="Medium",
        context=multi_context,
    )
    res4 = await multi_synth.generate_query_reference_prompt.generate(llm, data=cond4)
    generated_samples.append({
        "user_input": res4.query,
        "reference": res4.answer,
        "reference_contexts": multi_context,
        "synthesizer_type": "multi_hop_comparison",
        "persona": PERSONAS[2].name,
        "franchise": "Georgies Pizza vs Italianos",
        "location": "Auckland",
    })

    return generated_samples


def save_golden_dataset(
    samples: List[dict],
    json_path: Path = config.SYNTHETIC_GOLDEN_JSON,
    csv_path: Path = config.SYNTHETIC_GOLDEN_CSV,
) -> None:
    """Serialize golden cases to structured JSON and flattened CSV.

    Args:
        samples: Case dictionaries containing reference_contexts as a list of
            strings, plus question, reference, and any descriptive metadata.
        json_path: JSON destination; missing parent directories are created.
        csv_path: CSV destination; its parent directory must already exist.

    Returns:
        None. Overwrites both output files and prints their paths.

    Notes:
        rows contains copies of samples with reference_contexts joined by ' | '
        for CSV readability. JSON preserves the context lists. Input dictionaries
        are not modified.
    """
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(samples, f, indent=2)

    rows = []
    for s in samples:
        row = dict(s)
        row["reference_contexts"] = " | ".join(s["reference_contexts"])
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(csv_path, index=False)

    print(f"\nSynthetic golden dataset successfully saved:")
    print(f"  - JSON: {json_path}")
    print(f"  - CSV:  {csv_path}")


def load_or_generate_golden_dataset(
    force_regenerate: bool = False,
    json_path: Path = config.SYNTHETIC_GOLDEN_JSON,
) -> List[dict]:
    """Load cached golden cases or persist generated or curated replacements.

    Args:
        force_regenerate: If True, run fresh Ollama synthesis even if JSON exists.
            Any synthesis or initial save error triggers the curated seed fallback.
        json_path: Cache file to read or replace; its parents are created on save.

    Returns:
        A list of case dictionaries containing user_input, reference,
        reference_contexts, and synthesizer/persona/franchise/location metadata.

    Notes:
        A missing cache with force_regenerate=False uses SEED_SYNTHETIC_TESTSET
        directly, without contacting Ollama. Generation uses asyncio.run, which
        cannot run inside an existing event loop; that error also triggers the
        fallback. Writes use json_path and the default configured CSV destination.
        Cache parsing and fallback persistence errors propagate to the caller.
    """
    if not force_regenerate and json_path.exists():
        with open(json_path, "r") as f:
            samples = json.load(f)
        return samples

    if force_regenerate:
        try:
            samples = asyncio.run(generate_synthetic_samples_from_reviews())
            save_golden_dataset(samples, json_path=json_path)
            return samples
        except Exception as e:
            print(f"Ollama generation encountered an error: {e}")
            print("Falling back to curated Ragas synthesizer seed dataset...")

    save_golden_dataset(SEED_SYNTHETIC_TESTSET, json_path=json_path)
    return SEED_SYNTHETIC_TESTSET
