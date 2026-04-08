# Utilisation de l'image officielle Python 3.12 (version slim pour la légèreté)
FROM python:3.12-slim

# Définition du répertoire de travail dans le conteneur
WORKDIR /app

# Configuration des variables d'environnement pour Python
# Empêche la création de fichiers .pyc et assure que les logs sont affichés instantanément
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

# Installation des dépendances système de base
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copie du fichier requirements.txt
COPY requirements.txt .

# Installation des dépendances Python mentionnées dans votre fichier
RUN pip install --no-cache-dir -r requirements.txt

# Copie de tout le reste du code source (bot_telegram_baccara.py, utils_new.py, etc.)
COPY . .

# Création des dossiers nécessaires (logs et uploads) définis dans config.json
RUN mkdir -p logs uploads

# Exposition du port utilisé par web_server.py
EXPOSE 10000

# Commande de lancement du bot
CMD ["python", "bot_telegram_baccara.py"]
