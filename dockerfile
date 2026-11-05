# Utiliser une image Python officielle légère (slim)
FROM python:3.12-slim

# Définir le répertoire de travail dans le conteneur
WORKDIR /app

# Copier le fichier des dépendances en premier (pour optimiser le cache Docker)
COPY requirements.txt .

# Installer les dépendances Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copier le reste du code de l'application
COPY ./app /app/app

# Exposer le port sur lequel l'application écoute
EXPOSE 8000

# Commande pour démarrer l'application avec Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
