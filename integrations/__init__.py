"""Adapters to outside tools (defect trackers, requirement sources, the code host).

Each adapter takes an injectable HTTP session, so every test runs offline with a fake.
Nothing here posts, files or comments unless its caller asks explicitly.
"""
