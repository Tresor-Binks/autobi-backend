"""
SCHÉMAS PYDANTIC - AUTHENTICATION

Validation des données d'authentification.
"""

from pydantic import BaseModel, EmailStr
from app.schemas.user import UserResponse


# ============================================================================
# SCHÉMAS DE REQUÊTE
# ============================================================================

class LoginRequest(BaseModel):
    """Schéma pour la requête de connexion"""
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    """Schéma pour la requête d'inscription"""
    email: EmailStr
    password: str
    first_name: str
    last_name: str


# ============================================================================
# SCHÉMAS DE RÉPONSE
# ============================================================================

class AuthResponse(BaseModel):
    """
    Schéma de réponse pour login et register
    
    Retourne le token et les informations utilisateur
    """
    user: UserResponse
    token: str
    expires_at: str  # ISO 8601 format
    
    class Config:
        json_schema_extra = {
            "example": {
                "user": {
                    "id": 1,
                    "email": "user@example.com",
                    "first_name": "Jean",
                    "last_name": "Dupont",
                    "plan_type": "pay_as_you_go",
                    "token_balance": 5,
                    "created_at": "2024-01-01T10:00:00Z",
                    "last_login": None
                },
                "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "expires_at": "2024-01-01T10:30:00Z"
            }
        }


class TokenPayload(BaseModel):
    """Schéma du payload du JWT"""
    sub: int  # User ID
    exp: int  # Expiration timestamp