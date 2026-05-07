"""
MODÈLES SQLALCHEMY

Définition des tables de la base de données.
Compatible MySQL.
"""

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, 
    Text, Enum as SQLEnum, CheckConstraint
)
from sqlalchemy.dialects.mysql import JSON  # ← CHANGÉ : MySQL JSON au lieu de PostgreSQL JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.session import Base
import enum


# ============================================================================
# ENUMS
# ============================================================================

class PlanType(str, enum.Enum):
    PAY_AS_YOU_GO = "PAY_AS_YOU_GO"
    MONTHLY_UNLIMITED = "MONTHLY_UNLIMITED"


class AnalysisStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ============================================================================
# MODÈLE : USER
# ============================================================================

class User(Base):
    """
    Modèle utilisateur
    
    Stocke les informations d'authentification et de facturation.
    """
    __tablename__ = "users"
    
    # Identifiant
    id = Column(Integer, primary_key=True, index=True)
    
    # Informations personnelles
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    
    # Plan et facturation
    plan_type = Column(
        SQLEnum(PlanType),
        nullable=False,
        default=PlanType.PAY_AS_YOU_GO
    )
    token_balance = Column(Integer, nullable=False, default=5)
    
    # Dates
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    last_login = Column(DateTime, nullable=True)
    
    # Relations
    analyses = relationship("Analysis", back_populates="user", cascade="all, delete-orphan")
    token_transactions = relationship("TokenTransaction", back_populates="user", cascade="all, delete-orphan")
    
    # Contraintes
    __table_args__ = (
        CheckConstraint('token_balance >= 0', name='token_balance_positive'),
    )


# ============================================================================
# MODÈLE : ANALYSIS
# ============================================================================

class Analysis(Base):
    """
    Modèle d'analyse de fichier Excel
    
    Stocke les informations sur une analyse et ses résultats.
    """
    __tablename__ = "analyses"
    
    # Identifiant
    id = Column(Integer, primary_key=True, index=True)
    
    # Relation avec l'utilisateur
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Informations du fichier
    file_name = Column(String(255), nullable=False)
    file_size = Column(Integer, nullable=False)
    file_path = Column(String(500), nullable=True)
    
    # Statut
    status = Column(
        SQLEnum(AnalysisStatus),
        nullable=False,
        default=AnalysisStatus.PENDING,
        index=True
    )
    
    # Résultats (JSON flexible) - MySQL JSON au lieu de PostgreSQL JSONB
    results = Column(JSON, nullable=True)  # ← CHANGÉ
    error_message = Column(Text, nullable=True)
    
    # Tokens
    tokens_consumed = Column(Integer, default=0)
    
    # Dates
    created_at = Column(DateTime, server_default=func.now(), index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    
    # Relations
    user = relationship("User", back_populates="analyses")
    token_transactions = relationship("TokenTransaction", back_populates="analysis")
    
    # Contraintes
    __table_args__ = (
        CheckConstraint('file_size > 0', name='file_size_positive'),
        CheckConstraint('tokens_consumed >= 0', name='tokens_consumed_positive'),
    )


# ============================================================================
# MODÈLE : TOKEN_TRANSACTION
# ============================================================================

class TokenTransaction(Base):
    """
    Modèle de transaction de tokens
    
    Historique des achats et consommations de tokens.
    """
    __tablename__ = "token_transactions"
    
    # Identifiant
    id = Column(Integer, primary_key=True, index=True)
    
    # Relation avec l'utilisateur
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Type de transaction
    transaction_type = Column(String(20), nullable=False)  # 'purchase' ou 'consumption'
    
    # Montant
    amount = Column(Integer, nullable=False)
    
    # Relation avec une analyse (si consommation)
    analysis_id = Column(Integer, ForeignKey("analyses.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Description
    description = Column(Text, nullable=True)
    
    # Solde après transaction
    balance_after = Column(Integer, nullable=False)
    
    # Date
    created_at = Column(DateTime, server_default=func.now(), index=True)
    
    # Relations
    user = relationship("User", back_populates="token_transactions")
    analysis = relationship("Analysis", back_populates="token_transactions")
    
    # Contraintes
    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('purchase', 'consumption')",
            name='valid_transaction_type'
        ),
    )