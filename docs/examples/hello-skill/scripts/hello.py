#!/usr/bin/env python3
"""Pozdrowienie w formacie JSON.

Użycie: python scripts/hello.py --name Jan
Kody wyjścia: 0 = OK, 2 = brak wymaganego argumentu.
"""
import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Wypisuje pozdrowienie jako JSON.")
    parser.add_argument("--name", required=True, help="Imię do pozdrowienia")
    args = parser.parse_args()
    print(json.dumps({"greeting": f"Hello, {args.name}!"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
