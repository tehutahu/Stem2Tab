"""Write a new optional JSON comparison; baseline is the default."""
import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    """Copy bytes or apply C to explicitly acknowledged already-PR8 input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('baseline', 'frozen-c'), default='baseline')
    parser.add_argument('--already-pr8', action='store_true',
                        help='Acknowledge input already underwent PR8; this CLI does not run PR8')
    args = parser.parse_args(argv)
    source = args.input.resolve(strict=True)
    target = args.output.resolve()
    sidecar = target.with_name(target.name + '.decisions.json')
    if target == source or target.exists() or sidecar.exists():
        parser.error('Output and sidecar must be new paths distinct from input')
    if sidecar == source or not target.parent.is_dir():
        parser.error('Output parent must exist and sidecar must differ from input')
    if args.mode == 'frozen-c' and not args.already_pr8:
        parser.error('frozen-c requires --already-pr8 acknowledgement')
    payload = source.read_bytes()
    decision_payload = None
    if args.mode == 'frozen-c':
        from .frozen_c import additional_stage
        from .models import NoteEventSet
        result, decisions = additional_stage(NoteEventSet.model_validate_json(payload))
        payload = result.model_dump_json(indent=2).encode('utf-8')
        decision_payload = json.dumps(dict(mode=args.mode, decisions=decisions),
                                      indent=2, allow_nan=False).encode('utf-8')
    created: list[Path] = []
    try:
        with target.open('xb') as stream:
            created.append(target)
            stream.write(payload)
        if decision_payload is not None:
            with sidecar.open('xb') as stream:
                created.append(sidecar)
                stream.write(decision_payload)
    except BaseException:
        for path in reversed(created):
            path.unlink()
        raise


if __name__ == '__main__':
    main()
