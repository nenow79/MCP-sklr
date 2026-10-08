#!/usr/bin/env python3
"""Czeka podaną liczbę sekund (do pomiaru limitów czasu harnessa i timeoutu runnera).

Użycie: python scripts/sleep.py --seconds 30
Kody wyjścia: 0 = OK, 2 = błędne argumenty.
"""
import argparse
import json
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="Czeka N sekund i wypisuje JSON.")
    parser.add_argument("--seconds", type=int, required=True, help="Liczba sekund oczekiwania (0-600)")
    args = parser.parse_args()
    if not 0 <= args.seconds <= 600:
        print("Błąd: --seconds musi być w zakresie 0-600.", file=sys.stderr)
        return 2
    print(f"Start: czekam {args.seconds} s", file=sys.stderr)
    time.sleep(args.seconds)
    print(json.dumps({"slept": args.seconds}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
