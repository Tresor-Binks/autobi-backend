"""
SESSION BASE DE DONNÉES

Configuration de la connexion PostgreSQL avec SQLAlchemy.
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.core.config import settings


# ============================================================================
# CONFIGURATION SQLALCHEMY
# ============================================================================

# Moteur de base de données
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,  # Vérifie la connexion avant utilisation
    echo=settings.DEBUG   # Log SQL en mode debug
)

# Factory de sessions
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Base pour les modèles
Base = declarative_base()


# ============================================================================
# DÉPENDANCE POUR LES ROUTES
# ============================================================================

def get_db():
    """
    Générateur de session de base de données.
    
    À utiliser comme dépendance dans les routes FastAPI :
    db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()