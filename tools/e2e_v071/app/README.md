# tally

A deliberately tiny money-tallying library. It exists only as the throwaway app
for rite's v0.7.1 acceptance gate (`tools/e2e_v071` in the rite repository):
every ticket on its board is a small, mechanically verifiable change.

```sh
python -m pytest -q
```

Some tests fail on a fresh copy **by design**: each one is a ticket's agreed
verify, and it passes only once that ticket is delivered.
