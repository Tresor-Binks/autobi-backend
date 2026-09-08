"""
ROUTES D'AUTHENTIFICATION

Endpoints pour :
- Inscription
- Connexion
- Récupération du profil
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.database.session import get_db
from app.schemas.auth import RegisterRequest, LoginRequest, AuthResponse
from app.schemas.user import UserProfile, UserResponse
from app.services.user_service import (
    create_user,
    authenticate_user,
    update_last_login
)
from app.core.security import create_access_token
from app.core.config import settings
from app.dependencies.auth import get_current_user
from app.database.models import User


# ============================================================================
# ROUTER
# ============================================================================

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)


# ============================================================================
# ROUTE : INSCRIPTION
# ============================================================================

@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    db: Session = Depends(get_db)
):
    try:
        user = create_user(
            db=db,
            email=request.email,
            password=request.password,
            first_name=request.first_name,
            last_name=request.last_name
        )

        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        expires_at = datetime.utcnow() + access_token_expires

        access_token = create_access_token(
            data={"sub": str(user.id)},
            expires_delta=access_token_expires
        )

        return AuthResponse(
            user=UserResponse.model_validate(user),
            token=access_token,
            expires_at=expires_at.isoformat() + "Z"
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


# ============================================================================
# ROUTE : CONNEXION
# ============================================================================

@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    db: Session = Depends(get_db)
):
    user = authenticate_user(db, request.email, request.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = update_last_login(db, user)

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    expires_at = datetime.utcnow() + access_token_expires

    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=access_token_expires
    )

    return AuthResponse(
        user=UserResponse.model_validate(user),
        token=access_token,
        expires_at=expires_at.isoformat() + "Z"
    )


# ============================================================================
# ROUTE : PROFIL UTILISATEUR (CORRIGÉE)
# ============================================================================

@router.get("/me", response_model=UserProfile, response_model_exclude_unset=False)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user)
):
    # Forcer la sérialisation explicite de l'objet ORM SQLAlchemy
    return UserProfile.model_validate(current_user)


# ============================================================================
# ROUTE : VÉRIFICATION DU TOKEN
# ============================================================================

@router.get("/verify", status_code=status.HTTP_200_OK)
async def verify_token(
    current_user: User = Depends(get_current_user)
):
    return {
        "valid": True,
        "user_id": current_user.id,
        "email": current_user.email
    }