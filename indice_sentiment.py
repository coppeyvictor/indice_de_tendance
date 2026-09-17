import sys
import time

from sentiment import QuotaExceededError, analyze_sentiment


TEXTE_FINANCIER = (
    "Le conseil d'administration a validé un plan d'investissement de 50 millions d'euros "
    "pour la transition écologique des infrastructures de production. Bien que cette décision "
    "augmente l'endettement à court terme et ait provoqué une légère baisse de l'action ce matin, "
    "les analystes s'accordent à dire que ce positionnement ESG réduira les coûts énergétiques de 20% "
    "d'ici trois ans et anticipe parfaitement les nouvelles régulations européennes. Le climat social "
    "en interne s'est également nettement amélioré suite à cette annonce."
)


def main() -> int:
    print("Envoi de la requête en cours, démarrage du chronomètre...")
    debut = time.perf_counter()

    try:
        score = analyze_sentiment(TEXTE_FINANCIER)
    except ValueError as error:
        print(f"Réponse invalide : {error}", file=sys.stderr)
        return 1
    except QuotaExceededError as error:
        print(str(error), file=sys.stderr)
        print("Articles analysés : 0", file=sys.stderr)
        print(
            "Articles restants non analysés à cause de la limite : 1",
            file=sys.stderr,
        )
        return 1
    except Exception as error:
        print(f"Erreur lors de l'appel à Gemini : {error}", file=sys.stderr)
        return 1

    temps_ecoule = time.perf_counter() - debut
    print(f"Réponse de l'API : {score:.2f}")
    print(f"Temps de réponse : {temps_ecoule:.2f} secondes")
    print("Articles analysés : 1")
    print("Articles restants non analysés : 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
