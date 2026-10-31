# Ladybug implementation

To run the benchmark:

```bash
export SERIALIZED_DIR=/path/to/serialized/graphs  # required
python3 benchmark/lsqb/benchmark_runner.py
```

The runner downloads the requested LSQB dataset itself from
`https://datasets.ldbcouncil.org/lsqb/social-network-sf<sf>-projected-fk.tar.zst`
and caches the extracted CSV files under `/tmp/lsqb-datasets` (existing
downloads are reused on subsequent runs, so only the missing scale factor is
fetched).

The serialized graph is cached in `SERIALIZED_DIR` and is reused across runs
when the database version is unchanged.

## Environment variables

| Variable          | Default   | Description                                             |
|-------------------|-----------|---------------------------------------------------------|
| `SCALE_FACTOR`    | `100`     | LSQB dataset scale factor: `0.1`, `0.3`, `1`, `3`, `10`, `30`, `100` |
| `SERIALIZED_DIR`  | *(required)* | Directory that stores the serialized graphs         |
| `NUM_THREADS`     | CPU count | Number of threads used for query execution               |
| `TIMEOUT`         | unset     | Query timeout in milliseconds (queries run to completion if unset) |