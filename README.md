# Atlas OS

Atlas OS is a local-first Python scaffold for multi-division AI workflows with human approval gates.

The first implementation target is the GreenRock Analysts Monthly Report. Phase 0 uses mock/sample data only and does not connect to market data, email, client files, brokerage accounts, OneDrive, Gmail, or credential stores.

## Local Install

```bash
cd ~/Desktop/atlas-os
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

## Offline Local Run

If the editable install cannot fetch Python build tools, use the included local launcher:

```bash
cd ~/Desktop/atlas-os
export PATH="$PWD/bin:$PATH"
atlas --help
atlas status
atlas greenrock sample-report
```

## CLI

```bash
atlas --help
atlas status
atlas greenrock sample-report
```

You can also run the CLI without installing the console script:

```bash
python -m atlas_os.cli --help
python -m atlas_os.cli greenrock sample-report
```

## Safety Rule

Atlas OS must not publish, email, distribute, or otherwise release client-facing material without explicit human approval. Phase 0 only produces local sample output.

## Documentation

Planning documents live in [docs/README.md](docs/README.md).
