"""Load or create the golden evaluation dataset and print a case overview.

PROJECT_ROOT is added to sys.path for direct execution. --regenerate requests
fresh Ollama synthesis; otherwise an existing cache or curated seed is used.
Dataset creation writes configured JSON and CSV files. Importing src also
initializes the parent package's shared retrieval and generation resources.
"""

import argparse
import sys
from pathlib import Path

# Add project root to sys.path so config and src can be imported
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.golden_generator import load_or_generate_golden_dataset


def main():
    """Load or generate golden cases and print their questions and references.

    Reads --regenerate from the command line to request fresh Ollama synthesis.
    Without it, a missing dataset is populated from curated seed cases. samples
    contains case dictionaries with synthesizer, persona, and review context data.

    Returns:
        None. Dataset creation writes JSON and CSV through the dataset loader.
    """
    parser = argparse.ArgumentParser(description="Generate synthetic golden dataset using Ragas synthesizers")
    parser.add_argument("--regenerate", action="store_true", help="Force fresh synthesis using local Ollama")
    args = parser.parse_args()

    samples = load_or_generate_golden_dataset(force_regenerate=args.regenerate)

    print("\n" + "=" * 70)
    print(f"  Ragas Synthetic Golden Dataset ({len(samples)} Test Cases)")
    print("=" * 70)
    for i, s in enumerate(samples, 1):
        print(f"\n[Case {i}] ({s['synthesizer_type']} | Persona: {s['persona']})")
        print(f"  Question:  {s['user_input']}")
        print(f"  Reference: {s['reference']}")
        print(f"  Contexts:  {len(s['reference_contexts'])} ground-truth review excerpts")


if __name__ == "__main__":
    main()
