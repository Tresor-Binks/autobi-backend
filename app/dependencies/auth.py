"""
DÉPENDANCES D'AUTHENTIFICATION

Fonctions utilisées comme dépendances dans les routes FastAPI.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.database.models import User
from app.core.security import decode_access_token
from app.services.user_service import get_user_by_id


# ============================================================================
# SÉCURITÉ HTTP BEARER
# ============================================================================

# Schéma de sécurité pour Swagger UI
security = HTTPBearer()


# ============================================================================
# DÉPENDANCE : UTILISATEUR ACTUEL
# ============================================================================

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Récupère l'utilisateur actuellement authentifié à partir du token JWT.
    
    Cette fonction est utilisée comme dépendance dans les routes protégées :
    current_user: User = Depends(get_current_user)
    
    Args:
        credentials: Credentials HTTP Bearer (token)
        db: Session de base de données
        
    Returns:
        Utilisateur authentifié
        
    Raises:
        HTTPException 401: Si le token est invalide ou expiré
    """
    # Extraction du token
    token = credentials.credentials
    
    # Décodage du token
    payload = decode_access_token(token)
    
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Extraction de l'ID utilisateur
    user_id: int = payload.get("sub")
    
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Récupération de l'utilisateur
    user = get_user_by_id(db, user_id)
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur non trouvé",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return user


# ============================================================================
# DÉPENDANCE : VÉRIFICATION DU PLAN (POUR PLUS TARD)
# ============================================================================

def require_tokens(min_tokens: int):
    """
    Dépendance pour vérifier que l'utilisateur a suffisamment de tokens.
    
    Usage:
        @router.post("/analyze")
        async def analyze(user: User = Depends(require_tokens(10))):
            ...
    
    Args:
        min_tokens: Nombre minimum de tokens requis
        
    Returns:
        Fonction de dépendance
    """
    async def check_tokens(user: User = Depends(get_current_user)) -> User:
        if user.token_balance < min_tokens:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Solde de tokens insuffisant. Minimum requis : {min_tokens}"
            )
        return user
    
    return check_tokens