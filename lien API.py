import sys

from sentiment import analyze_sentiment


def main() -> int:
    text = "La banque centrale a maintenu ses taux."

    try:
        score = analyze_sentiment(text)
    except Exception as error:
        print(f"Erreur lors de l'appel à Gemini : {error}", file=sys.stderr)
        return 1

    print(f"Réponse de l'API : {score:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
