"""
SCHÉMAS PYDANTIC - USER

Validation des données utilisateur pour les requêtes et réponses API.
"""

from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Optional
from app.database.models import PlanType


# ============================================================================
# SCHÉMAS DE BASE
# ============================================================================

class UserBase(BaseModel):
    """Schéma de base pour un utilisateur"""
    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)


# ============================================================================
# SCHÉMAS DE CRÉATION
# ============================================================================

class UserCreate(UserBase):
    """Schéma pour la création d'un utilisateur (inscription)"""
    password: str = Field(..., min_length=6, max_length=100)


# ============================================================================
# SCHÉMAS DE RÉPONSE
# ============================================================================

class UserResponse(UserBase):
    """
    Schéma de réponse utilisateur
    
    Retourné par l'API - N'inclut PAS le mot de passe
    """
    id: int
    plan_type: PlanType
    token_balance: int
    created_at: datetime
    last_login: Optional[datetime] = None
    
    class Config:
        from_attributes = True  # Permet la conversion depuis les modèles SQLAlchemy


class UserProfile(BaseModel):
    """
    Schéma détaillé du profil utilisateur
    
    Utilisé pour /auth/me
    """
    id: int
    email: EmailStr
    first_name: str
    last_name: str
    plan_type: PlanType
    token_balance: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    
    class Config:
        from_attributes = True