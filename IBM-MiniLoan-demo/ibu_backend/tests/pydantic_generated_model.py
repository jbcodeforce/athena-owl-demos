# generated by datamodel-codegen:
#   filename:  Production_deploymentLoan_validation_productionDecisionService.yaml
#   timestamp: 2024-06-21T12:29:44+00:00

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class Bankruptcy(BaseModel):
    date: Optional[datetime] = None
    chapter: Optional[int] = None
    reason: Optional[str] = None


class SSN(BaseModel):
    areaNumber: Optional[str] = None
    groupCode: Optional[str] = None
    serialNumber: Optional[str] = None





class Error(BaseModel):
    code: Optional[int] = Field(None, description='HTTP error code.')
    message: Optional[str] = Field(None, description='Error message.')
    details: Optional[str] = Field(None, description='Detailed error message.')
    errorCode: Optional[str] = Field(None, description='Product error code.')

class LoanRequest(BaseModel):
    numberOfMonthlyPayments: Optional[int] = None
    startDate: Optional[datetime] = None
    amount: Optional[int] = None
    loanToValue: Optional[float] = None
    
class Borrower(BaseModel):
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    birth: Optional[datetime] = None
    SSN: Optional[SSN] = None
    yearlyIncome: Optional[int] = None
    zipCode: Optional[str] = None
    creditScore: Optional[int] = None
    spouse: Optional[Borrower] = None
    latestBankruptcy: Optional[Bankruptcy] = None


class Report(BaseModel):
    borrower: Optional[Borrower] = None
    loan: Optional[LoanRequest] = None
    validData: Optional[bool] = None
    insuranceRequired: Optional[bool] = None
    insuranceRate: Optional[float] = None
    approved: Optional[bool] = None
    messages: Optional[List[str]] = None
    yearlyInterestRate: Optional[float] = None
    monthlyRepayment: Optional[float] = None
    insurance: Optional[str] = None
    message: Optional[str] = None
    yearlyRepayment: Optional[float] = None


class Request(BaseModel):
    field__DecisionID__: Optional[str] = Field(
        None,
        alias='__DecisionID__',
        description='Unique identifier representing the execution of the decision service operation. If it is not specified, it will be computed automatically.',
    )
    borrower: Optional[Borrower] = None
    loan: Optional[LoanRequest] = None


class Response(BaseModel):
    field__DecisionID__: Optional[str] = Field(
        None,
        alias='__DecisionID__',
        description='Unique identifier representing the execution of the decision service operation. If it is not specified, it will be computed automatically.',
    )
    report: Optional[Report] = None


Borrower.update_forward_refs()
