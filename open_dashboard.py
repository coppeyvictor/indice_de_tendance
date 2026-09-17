#!/usr/bin/env python3
import argparse
import webbrowser
from pathlib import Path

from fear_greed_index import generate_interactive_fear_greed_chart


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Génère et ouvre le Fear & Greed interactif dans le navigateur."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Nombre de jours à afficher (défaut : 30).",
    )
    parser.add_argument(
        "--theme",
        choices=("finance", "esg", "ecology"),
        default="finance",
        help="Thème à afficher : finance, esg ou ecology.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Génère le fichier sans ouvrir le navigateur.",
    )
    args = parser.parse_args()

    output_path = Path("fear_greed_chart.html").resolve()
    generate_interactive_fear_greed_chart(str(output_path), args.days, theme=args.theme)

    print(f"Dashboard interactif : {output_path}")
    if not args.no_open:
        candidate_urls = [
            output_path.as_uri(),
            f"file://{output_path}",
        ]
        opened = False
        for url in candidate_urls:
            try:
                if webbrowser.open(url):
                    opened = True
                    break
            except Exception:
                continue
        if not opened:
            print(f"Ouvrez le fichier manuellement : {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
