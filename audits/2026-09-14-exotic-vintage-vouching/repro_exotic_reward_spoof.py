#!/usr/bin/env python3
"""Offline SOURCE-RED reproducer for RustChain exotic-tier claim vouching.

This script never imports or starts the RustChain node and makes no network
calls.  It parses only the detector and tiny helper definitions from a local
copy of node/rustchain_v2_integrated_v2.2.1_rip200.py, then shows that modern
x86-shaped metadata can select premium SPARC/M68K identities through claimed
family/arch strings.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import typing

TARGET_FUNCS = {
    "_has_any_token",
    "_claimed_family_and_arch",
    "_cpu_brand_string",
    "_detect_exotic_arch",
}


def load_detector(path: pathlib.Path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in TARGET_FUNCS
    ]
    found = {node.name for node in selected}
    missing = TARGET_FUNCS - found
    if missing:
        raise SystemExit(f"source shape changed; missing helpers: {sorted(missing)}")

    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"Optional": typing.Optional}
    exec(compile(module, str(path), "exec"), namespace, namespace)
    return namespace["_detect_exotic_arch"]


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {pathlib.Path(sys.argv[0]).name} /path/to/node/rustchain_v2_integrated_v2.2.1_rip200.py", file=sys.stderr)
        return 2

    path = pathlib.Path(sys.argv[1])
    detect = load_detector(path)

    modern = {
        "machine": "x86_64",
        "cpu": "Intel(R) Core(TM) i9-13900K",
        "family": "x86_64",
        "arch": "modern",
    }
    fake_sparc = {**modern, "family": "sparc", "arch": "sparc_v7"}
    fake_m68k = {**modern, "family": "m68k", "arch": "68000"}

    control = detect(modern)
    sparc = detect(fake_sparc)
    m68k = detect(fake_m68k)

    print("modern control:", control)
    print("modern x86 metadata + claimed SPARC:", sparc)
    print("modern x86 metadata + claimed M68K:", m68k)

    assert control is None, f"unexpected modern control classification: {control!r}"
    assert sparc == {"device_family": "SPARC", "device_arch": "sparc_v7"}, sparc
    assert m68k == {"device_family": "M68K", "device_arch": "68000"}, m68k

    print("SOURCE RED: client-controlled family/arch claims cross the exotic detector trust boundary.")
    print("No production endpoint was contacted; no chain state was read or modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
