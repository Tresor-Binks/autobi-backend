"""
SERVICE UTILISATEUR

Logique métier pour la gestion des utilisateurs.
"""

from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
from app.database.models import User, PlanType
from app.core.security import hash_password, verify_password
from app.core.config import settings


# ============================================================================
# CRÉATION D'UTILISATEUR
# ============================================================================

def create_user(
    db: Session,
    email: str,
    password: str,
    first_name: str,
    last_name: str
) -> User:
    """
    Crée un nouvel utilisateur dans la base de données.
    
    Args:
        db: Session de base de données
        email: Email de l'utilisateur
        password: Mot de passe en clair
        first_name: Prénom
        last_name: Nom
        
    Returns:
        Utilisateur créé
        
    Raises:
        ValueError: Si l'email existe déjà
    """
    # Vérifier si l'email existe déjà
    existing_user = get_user_by_email(db, email)
    if existing_user:
        raise ValueError("Un utilisateur avec cet email existe déjà")
    
    # Hashing du mot de passe
    password_hash = hash_password(password)
    
    # Création de l'utilisateur
    db_user = User(
        email=email,
        password_hash=password_hash,
        first_name=first_name,
        last_name=last_name,
        plan_type=PlanType.PAY_AS_YOU_GO,
        token_balance=settings.DEFAULT_TOKEN_BALANCE
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    return db_user


# ============================================================================
# RÉCUPÉRATION D'UTILISATEUR
# ============================================================================

def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """
    Récupère un utilisateur par son email.
    
    Args:
        db: Session de base de données
        email: Email de l'utilisateur
        
    Returns:
        Utilisateur trouvé ou None
    """
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """
    Récupère un utilisateur par son ID.
    
    Args:
        db: Session de base de données
        user_id: ID de l'utilisateur
        
    Returns:
        Utilisateur trouvé ou None
    """
    return db.query(User).filter(User.id == user_id).first()


# ============================================================================
# AUTHENTIFICATION
# ============================================================================

def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """
    Authentifie un utilisateur avec email et mot de passe.
    
    Args:
        db: Session de base de données
        email: Email de l'utilisateur
        password: Mot de passe en clair
        
    Returns:
        Utilisateur authentifié ou None si échec
    """
    user = get_user_by_email(db, email)
    
    if not user:
        return None
    
    if not verify_password(password, user.password_hash):
        return None
    
    return user


def update_last_login(db: Session, user: User) -> User:
    """
    Met à jour la date de dernière connexion.
    
    Args:
        db: Session de base de données
        user: Utilisateur à mettre à jour
        
    Returns:
        Utilisateur mis à jour
    """
    user.last_login = datetime.utcnow()
    db.commit()
    db.refresh(user)
    
    return user


# ============================================================================
# GESTION DES TOKENS (PRÉVU POUR PLUS TARD)
# ============================================================================

def deduct_tokens(db: Session, user: User, amount: int) -> User:
    """
    Déduit des tokens du solde de l'utilisateur.
    
    Args:
        db: Session de base de données
        user: Utilisateur
        amount: Nombre de tokens à déduire
        
    Returns:
        Utilisateur mis à jour
        
    Raises:
        ValueError: Si solde insuffisant
    """
    if user.token_balance < amount:
        raise ValueError("Solde de tokens insuffisant")
    
    user.token_balance -= amount
    db.commit()
    db.refresh(user)
    
    return user


def add_tokens(db: Session, user: User, amount: int) -> User:
    """
    Ajoute des tokens au solde de l'utilisateur.
    
    Args:
        db: Session de base de données
        user: Utilisateur
        amount: Nombre de tokens à ajouter
        
    Returns:
        Utilisateur mis à jour
    """
    user.token_balance += amount
    db.commit()
    db.refresh(user)
    
    return user