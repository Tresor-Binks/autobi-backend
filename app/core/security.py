"""
SÉCURITÉ - JWT & HASHING

Fonctions pour :
- Hashing des mots de passe (argon2)
- Génération et vérification des JWT
"""

from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.core.config import settings


# ============================================================================
# HASHING DES MOTS DE PASSE
# ============================================================================

# Context de hashing (argon2 au lieu de bcrypt)
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    """
    Hash un mot de passe en utilisant argon2.
    
    Args:
        password: Mot de passe en clair
        
    Returns:
        Mot de passe hashé
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Vérifie qu'un mot de passe correspond au hash.
    
    Args:
        plain_password: Mot de passe en clair
        hashed_password: Mot de passe hashé
        
    Returns:
        True si le mot de passe est correct
    """
    return pwd_context.verify(plain_password, hashed_password)


# ... (garder le reste du fichier inchangé)


# ============================================================================
# JWT - JSON WEB TOKENS
# ============================================================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Crée un JWT access token.
    
    Args:
        data: Données à encoder dans le token (ex: {"sub": user_id})
        expires_delta: Durée de validité personnalisée
        
    Returns:
        Token JWT encodé
    """
    to_encode = data.copy()
    
    # Calcul de l'expiration
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    
    # Ajout du timestamp d'expiration
    to_encode.update({"exp": expire})
    
    # Encodage du token
    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
    )
    
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """
    Décode et vérifie un JWT access token.
    
    Args:
        token: Token JWT à décoder
        
    Returns:
        Payload du token si valide, None sinon
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError:
        return None

def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        print(f"✅ Token décodé : {payload}")
        return payload
    except JWTError as e:
        print(f"❌ Erreur JWT : {e}")
        return None