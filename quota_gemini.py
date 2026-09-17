import os
import re
import sys

from google import genai

from sentiment import MODEL_NAME


def extract_detail(message: str, pattern: str, default: str = "inconnu") -> str:
    match = re.search(pattern, message)
    return match.group(1) if match else default


def main() -> int:
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        print("La variable GEMINI_API_KEY est absente.", file=sys.stderr)
        return 1

    try:
        client = genai.Client(api_key=api_key)
        client.models.generate_content(
            model=MODEL_NAME,
            contents="Réponds uniquement par OK.",
        )
    except Exception as error:
        message = str(error)
        upper_message = message.upper()

        if "429" in upper_message or "RESOURCE_EXHAUSTED" in upper_message:
            quota_value = extract_detail(message, r"quotaValue['\"]?\s*[:=]\s*['\"]?([0-9]+)")
            retry_delay = extract_detail(message, r"retryDelay['\"]?\s*[:=]\s*['\"]?([^,'\"}]+)")
            print("Quota Gemini atteint.")
            print(f"Limite indiquée par l'API : {quota_value} requêtes")
            print(f"Modèle vérifié : {MODEL_NAME}")
            print(f"Nouvelle tentative indiquée dans : {retry_delay}")
            return 2

        print(f"Impossible de vérifier le quota : {error}", file=sys.stderr)
        return 1

    print(f"La requête Gemini a été acceptée pour le modèle {MODEL_NAME}.")
    print("La limite exacte et le quota restant ne sont pas exposés par cette API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
