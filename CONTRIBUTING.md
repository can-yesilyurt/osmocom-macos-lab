# Contributing

Please report the Mac model, macOS/toolchain versions, SDR model, source pins
and the exact stage or command that failed. Include a short redacted excerpt
instead of whole runtime logs. Never include SIM keys, subscriber identifiers,
phone numbers, SMS content, hardware serials or subscriber databases.

For a code change, run:

```sh
python3 -B -m unittest discover -s tests -v
python3 scripts/check_publication.py
```

For build or runtime changes, also rebuild the affected stage and run
`python3 -B scripts/smoke_native.py` with the management ports free. Changes to
source pins require a fresh state directory and an updated validation report.
Describe skipped tests explicitly. Never label an RF or handset test complete
on the basis of a successful compiler run or the radio's management response.

The native build workflow is manually triggered. It exercises software and
loopback links; handset tests need a local radio setup. When reporting a new
hardware result, separate daemon-observed registration, operator-confirmed
voice/SMS, independent RF measurements and tests that remain unperformed.

Before committing or pushing, inspect staged files and run:

```sh
python3 scripts/check_publication.py --staged
```

The publication check catches common local artifacts and identifier patterns;
it does not prove that arbitrary logs are safe to publish. Keep raw logs out
of the repository. Follow the licenses of the files and external projects you
modify, and credit the original Osmocom and Darwin port work.
