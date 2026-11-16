"""Launch the pizza feedback CLI through its original entry point.

Running this file calls src.cli.main. Importing it exposes main without
starting the input loop, but importing src initializes its shared model and
vector store resources.
"""

from src.cli import main


if __name__ == "__main__":
    main()
