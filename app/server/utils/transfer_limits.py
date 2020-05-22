from typing import List, Callable, Optional, Union, Tuple, Iterable
from abc import ABC, abstractmethod

import datetime
from functools import reduce

from sqlalchemy import or_
from sqlalchemy.orm import Query
from sqlalchemy.sql import func

from server.exceptions import (
    NoTransferAllowedLimitError,
    MaximumPerTransferLimitError,
    MinimumSentLimitError,
    TransferAmountLimitError,
    TransferCountLimitError,
    TransferBalanceFractionLimitError)

from server.models import token
from server import db
from server.sempo_types import TransferAmount
from server.models.credit_transfer import CreditTransfer
from server.utils.transfer_enums import TransferSubTypeEnum, TransferTypeEnum, TransferStatusEnum
from server.utils.access_control import AccessControl

import config

AppliedToTypes = List[Union[TransferTypeEnum, Tuple[TransferTypeEnum, TransferSubTypeEnum]]]
AggregateAvailability = Union[int, TransferAmount]
ApplicationFilter = Callable[[CreditTransfer], bool]
AggregationFilter = Callable[[CreditTransfer], list]
QueryConstructorFunc = Callable[[CreditTransfer, Query], Query]

PAYMENT = TransferTypeEnum.PAYMENT
DEPOSIT = TransferTypeEnum.DEPOSIT
WITHDRAWAL = TransferTypeEnum.WITHDRAWAL

AGENT_OUT = TransferSubTypeEnum.AGENT_OUT
AGENT_IN = TransferSubTypeEnum.AGENT_IN
STANDARD = TransferSubTypeEnum.STANDARD
RECLAMATION = TransferSubTypeEnum.RECLAMATION

STANDARD_PAYMENT = (PAYMENT, STANDARD)
AGENT_OUT_PAYMENT = (PAYMENT, AGENT_OUT)
AGENT_IN_PAYMENT = (PAYMENT, AGENT_IN)
RECLAMATION_PAYMENT = (PAYMENT, RECLAMATION)

GENERAL_PAYMENTS = [STANDARD_PAYMENT, AGENT_OUT_PAYMENT, AGENT_IN_PAYMENT, RECLAMATION_PAYMENT]


def combine_filter_lists(filter_lists: List[List]) -> List:
    return reduce(lambda f, i: f + i, filter_lists, [])

# ~~~~~~SIMPLE CHECKS~~~~~~


def sempo_admin_involved(credit_transfer):
    if credit_transfer.recipient_user and AccessControl.has_sufficient_tier(
            credit_transfer.recipient_user.roles, 'ADMIN', 'sempoadmin'):
        return True

    if credit_transfer.sender_user and AccessControl.has_sufficient_tier(
            credit_transfer.sender_user.roles, 'ADMIN', 'sempoadmin'):
        return True

    return False


def sender_user_exists(credit_transfer: CreditTransfer):
    return credit_transfer.sender_user


def user_has_group_account_role(credit_transfer):
    return credit_transfer.sender_user.has_group_account_role


def user_phone_is_verified(credit_transfer):
    return credit_transfer.sender_user.is_phone_verified


def user_individual_kyc_is_verified(credit_transfer):
    return _sender_matches_kyc_criteria(
        credit_transfer,
        lambda app: app.kyc_status == 'VERIFIED' and app.type == 'INDIVIDUAL' and not app.multiple_documents_verified
    )


def user_business_or_multidoc_kyc_verified(credit_transfer):
    return _sender_matches_kyc_criteria(
        credit_transfer,
        lambda app: app.kyc_status == 'VERIFIED'
                    and (app.type == 'BUSINESS' or app.multiple_documents_verified)
    )


def _sender_matches_kyc_criteria(credit_transfer, criteria):
    if credit_transfer.sender_user is not None:
        matches_criteria = next((app for app in credit_transfer.sender_user.kyc_applications if criteria(app)), None)
        return bool(matches_criteria)
    return False


def token_is_liquid_type(credit_transfer):
    return credit_transfer.token and credit_transfer.token.token_type is token.TokenType.LIQUID


def token_is_reserve_type(credit_transfer):
    return credit_transfer.token and credit_transfer.token.token_type is token.TokenType.RESERVE


def transfer_is_agent_out_subtype(credit_transfer):
    return credit_transfer.transfer_subtype is TransferSubTypeEnum.AGENT_OUT


# ~~~~~~COMPOSITE CHECKS~~~~~~
def base_check(credit_transfer):
    # Only ever check transfers with a sender user and no sempoadmin (those involving sempoadmins are always allowed)
    return sender_user_exists(credit_transfer) and not sempo_admin_involved(credit_transfer)


def is_user_and_any_token(credit_transfer):
    return base_check(credit_transfer) \
           and (token_is_reserve_type(credit_transfer) or token_is_liquid_type(credit_transfer))


def is_user_and_liquid_token(credit_transfer):
    return base_check(credit_transfer) and token_is_liquid_type(credit_transfer) \
           and not user_has_group_account_role(credit_transfer)


def is_group_and_liquid_token(credit_transfer):
    return base_check(credit_transfer) and token_is_liquid_type(credit_transfer) \
           and user_has_group_account_role(credit_transfer)


def is_any_token_and_user_is_not_phone_and_not_kyc_verified(credit_transfer):
    return is_user_and_any_token(credit_transfer) and \
           not user_phone_is_verified(credit_transfer) and not user_individual_kyc_is_verified(credit_transfer)


def is_any_token_and_user_is_phone_but_not_kyc_verified(credit_transfer):
    return is_user_and_any_token(credit_transfer) and user_phone_is_verified(credit_transfer) and not (
            user_individual_kyc_is_verified(credit_transfer) or user_business_or_multidoc_kyc_verified(credit_transfer)
    )


def is_any_token_and_user_is_kyc_verified(credit_transfer):
    return is_user_and_any_token(credit_transfer) and user_individual_kyc_is_verified(credit_transfer)


def is_any_token_and_user_is_kyc_business_verified(credit_transfer):
    return is_user_and_any_token(credit_transfer) and user_business_or_multidoc_kyc_verified(credit_transfer)


# ~~~~~~LIMIT FILTERS~~~~~~


def after_time_period_filter(days: int):
    epoch = datetime.datetime.today() - datetime.timedelta(days=days)
    return [CreditTransfer.created >= epoch]


def matching_sender_user_filter(transfer: CreditTransfer):
    return [CreditTransfer.sender_user == transfer.sender_user]


def regular_payment_filter(transfer: CreditTransfer):
    return [CreditTransfer.transfer_subtype == TransferSubTypeEnum.STANDARD]


def matching_transfer_type_filter(transfer: CreditTransfer):
    return [CreditTransfer.transfer_type == transfer.transfer_type]


def matching_transfer_type_and_subtype_filter(transfer: CreditTransfer):
    return [
        CreditTransfer.transfer_type == transfer.transfer_type,
        CreditTransfer.transfer_subtype == transfer.transfer_subtype
    ]


def withdrawal_or_agent_out_and_not_excluded_filter(transfer: CreditTransfer):
    return [
        or_(CreditTransfer.transfer_type == TransferTypeEnum.WITHDRAWAL,
            CreditTransfer.transfer_subtype == TransferSubTypeEnum.AGENT_OUT),
        CreditTransfer.exclude_from_limit_calcs == False
    ]


def not_rejected_filter():
    return [CreditTransfer.transfer_status != TransferStatusEnum.REJECTED]


class BaseTransferLimit(ABC):
    """
    Base Limit Class. All limits use `applies_to_transfer` to determine if they are applied.
    Specific Limit rules vary hugely, so this class is pretty sparse,
    however, all limits follow the same overall process:
    1. Check if the limit applies to the transfer, based off the a) Type/Subtype & b) Query Filters
    2. Calculate how large the limit is (may be fixed, or variable)
    3. Calculate how much of the limit is available (this may involve aggregating across prev transfers and subtracting)
    4. Calculate how much the current transfer cases uses
    5. Check if the current case uses more than what's available
    """

    @abstractmethod
    def available(self, transfer: CreditTransfer) -> AggregateAvailability:
        """
        How much of the limit is still available. Uses a CreditTransfer object for context.
        Is generally an amount, but can also be something like a number of transfers.
        :param transfer: the transfer in question
        :return: Count or Amount
        """
        pass

    @abstractmethod
    def case_will_use(self, transfer: CreditTransfer) -> AggregateAvailability:
        """
        How much of the limit will be used in this particular transfer case.
        Is generally an amount, but can also be something like a number of transfers.
        :param transfer: the transfer in question
        :return: count or TransferAmount
        """

    @abstractmethod
    def throw_validation_error(self, transfer: CreditTransfer, available: AggregateAvailability):
        """
        Throws some sort of TransferLimitError
        """
        pass

    def validate_transfer(self, transfer: CreditTransfer):
        """
        Will raise an exception if the provided transfer doesn't pass this limit
        :param transfer: the transfer you wish to validate
        :return: Nothing, just throws the appropriate error if the transfer doesn't pass
        """

        available = self.available(transfer)
        if available < self.case_will_use(transfer):
            self.throw_validation_error(transfer, available)

    def applies_to_transfer(self, transfer: CreditTransfer) -> bool:
        """
        Determines if the limit applies to the given transfer. Uses a two step process:

        - Include transfer only if it matches either a Type or a (Type,Subtype) tuple from applied_to_transfer_types
        - Include transfer only if calling `application_filter` on the transfer returns true

        :param transfer: the credit transfer in question
        :return: boolean of whether the limit is applied or not
        """
        return (
                       transfer.transfer_type in self.applied_to_transfer_types
                       or (transfer.transfer_type, transfer.transfer_subtype) in self.applied_to_transfer_types
               ) and self.application_filter(transfer)

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.name}>"

    def __init__(self,
                 name: str,
                 applied_to_transfer_types: AppliedToTypes,
                 application_filter: ApplicationFilter,
                 ):
        """
        :param name: Human-friendly name
        :param applied_to_transfer_types: list which each item is either a
         TransferType or a (TransferType ,TransferSubType) tuple
        :param application_filter: one of the base or composite checks listed at the top of this file
        """

        self.name = name

        # Force to list of tuples to ensure the use of 'in' behaves as expected
        self.applied_to_transfer_types = [tuple(t) if isinstance(t, list) else t for t in applied_to_transfer_types]
        self.application_filter = application_filter


class NoTransferAllowedLimit(BaseTransferLimit):
    """
    A special limit that always throws a 'NoTransferAllowedLimitError' exception when `validate_transfer` is called
    """

    def available(self, transfer: CreditTransfer) -> AggregateAvailability:
        return 0

    def case_will_use(self, transfer: CreditTransfer) -> int:
        return 1

    def throw_validation_error(self, transfer: CreditTransfer, available: AggregateAvailability):
        raise NoTransferAllowedLimitError(token=transfer.token.name)

    def __init__(
            self,
            name: str,
            applied_to_transfer_types: AppliedToTypes,
            application_filter: ApplicationFilter,
    ):
        super().__init__(
            name,
            applied_to_transfer_types,
            application_filter
        )


class MaximumAmountPerTransferLimit(BaseTransferLimit):
    """
    A limit that specifies a maximum amount that can be transferred per transfer
    """

    def available(self, transfer: CreditTransfer) -> AggregateAvailability:
        return self.maximum_amount

    def case_will_use(self, transfer: CreditTransfer) -> AggregateAvailability:
        return transfer.transfer_amount

    def throw_validation_error(self, transfer: CreditTransfer, available: AggregateAvailability):
        message = 'Maximum Per Transfer Exceeded (Limit {}). {} available'.format(self.name, self.maximum_amount)

        raise MaximumPerTransferLimitError(
            message=message,
            maximum_amount_limit=self.maximum_amount,
            token=transfer.token.name)

    def __init__(
            self,
            name: str,
            applied_to_transfer_types: AppliedToTypes,
            application_filter: ApplicationFilter,
            maximum_amount: TransferAmount
    ):
        super().__init__(
            name,
            applied_to_transfer_types,
            application_filter,
        )

        self.maximum_amount = int(maximum_amount * config.LIMIT_EXCHANGE_RATE)


class AggregateLimit(BaseTransferLimit):
    """
    A transfer limit that uses an aggregate (count, total sent etc) of previous transfers over some time period
    for its available amount criteria.
    """

    @abstractmethod
    def available_base(self, transfer: CreditTransfer) -> AggregateAvailability:
        """
        Calculate the how much base availability there is
        :param transfer: the credit transfer in question
        :return: Either a transfer amount or a count
        """
        pass
    
    @abstractmethod
    def used_aggregator(
            self,
            transfer: CreditTransfer,
            query_constructor: QueryConstructorFunc
    ) -> AggregateAvailability:
        """
        The aggregator function is used to calculate the how much of the availability has been consumed.
        Takes the query_constructor as an input rather than accessing via 'self' as it allows us to easily create
        mixins for various types of aggregation (for example total transfer amounts).
        :param transfer: the credit transfer in question
        :param query_constructor: the query constructor as defined below.
        :return: Either a transfer amount or transfer count
        """
        pass

    def available(self, transfer: CreditTransfer) -> AggregateAvailability:
        base = self.available_base(transfer)
        used = self.used_aggregator(
            transfer,
            self.query_constructor
        )

        return base - used

    def query_constructor_filter_specifiable(
            self,
            transfer: CreditTransfer,
            base_query: Query,
            custom_filter: AggregationFilter) -> Query:
        """
        Constructs a filtered query for aggregation, where the last filter step can be provided by the user
        :param transfer:
        :param base_query:
        :param custom_filter:
        :return: An SQLAlchemy Query Object
        """

        filter_list = combine_filter_lists(
            [
                matching_sender_user_filter(transfer),
                not_rejected_filter(),
                after_time_period_filter(self.time_period_days),
                custom_filter(transfer)
            ]
        )

        return base_query.filter(*filter_list)

    def query_constructor(
            self,
            transfer: CreditTransfer,
            base_query: Query,
    ) -> Query:
        """
        Acts as a partial for `query_constructor_filter_specifiable`, with the custom aggregation filter set to that
        provided to the limit on instantiation. We don't use an actual partial because mypy type checking won't work.
        :param transfer:
        :param base_query:
        :return: An SQLAlchemy Query Object
        """

        return self.query_constructor_filter_specifiable(transfer, base_query, self.custom_aggregation_filter)

    def __init__(self,
                 name: str,
                 applied_to_transfer_types: AppliedToTypes,
                 application_filter: ApplicationFilter,
                 time_period_days: int,
                 aggregation_filter: AggregationFilter = matching_transfer_type_filter
                 ):
        """
        :param name:
        :param applied_to_transfer_types:
        :param application_filter:
        :param time_period_days: How many days back to include in aggregation
        :param aggregation_filter: An SQLAlchemy Query Filter
        """

        super().__init__(name, applied_to_transfer_types, application_filter)

        self.time_period_days = time_period_days

        self.custom_aggregation_filter = aggregation_filter


class AggregateTransferAmountMixin(object):
    """
    A Mixin for aggregating over transfer amounts. Use it alongside the AggregateLimit class
    """

    @staticmethod
    def used_aggregator(transfer: CreditTransfer, query_constructor: QueryConstructorFunc):
        # We need to sub the transfer amount from the allowance because it's hard to exclude it from the aggregation
        return query_constructor(
            transfer,
            db.session.query(func.sum(CreditTransfer.transfer_amount).label('total'))
        ).execution_options(show_all=True).first().total - int(transfer.transfer_amount)

    @staticmethod
    def case_will_use(transfer: CreditTransfer) -> TransferAmount:
        return transfer.transfer_amount


class TotalAmountLimit(AggregateTransferAmountMixin, AggregateLimit):
    """
    A limit that where the maxium amount that can be transferred over a set of transfers within a time period is fixed.
    Eg "You can transfer a maximum of 400 Foo token every 10 days"
    """

    def available_base(self, transfer: CreditTransfer) -> TransferAmount:
        return self._total_amount

    def throw_validation_error(self, transfer: CreditTransfer, available: AggregateAvailability):
        message = 'Account Limit "{}" reached. {} available'.format(self.name, max(available, 0))

        raise TransferAmountLimitError(
            transfer_amount_limit=self.available_base(transfer),
            transfer_amount_avail=available,
            limit_time_period_days=self.time_period_days,
            token=transfer.token.name,
            message=message
        )

    def __init__(
            self,
            name: str,
            applied_to_transfer_types: AppliedToTypes,
            application_filter: ApplicationFilter,
            time_period_days: int,
            total_amount: TransferAmount,
            aggregation_filter: AggregationFilter = matching_transfer_type_filter
    ):
        super().__init__(
            name,
            applied_to_transfer_types,
            application_filter,
            time_period_days,
            aggregation_filter
        )

        # TODO: Make LIMIT_EXCHANGE_RATE configurable per org
        self._total_amount: TransferAmount = int(total_amount * config.LIMIT_EXCHANGE_RATE)


class MinimumSentLimit(AggregateTransferAmountMixin, AggregateLimit):
    """
    A limit that where the maximum amount that can be transferred over a set of transfers within a time period must be
    less than the amount sent of some other transfer type (defaults to regular payments) in that same time period
    Eg "You have made 300 FooTokens worth of payments in the last 7 days, so you can make a maximum of 300 Footokens
    worth of withdrawals"
    """

    def available_base(self, transfer: CreditTransfer) -> TransferAmount:
        return self.query_constructor_filter_specifiable(
            transfer,
            db.session.query(func.sum(CreditTransfer.transfer_amount).label('total')),
            self.minimum_sent_aggregation_filter
        ).execution_options(show_all=True).first().total or 0

    def throw_validation_error(self, transfer: CreditTransfer, available: AggregateAvailability):
        message = 'Account Limit "{}" reached. {} available'.format(self.name, max(int(available), 0))

        raise MinimumSentLimitError(
            transfer_amount_limit=self.available_base(transfer),
            transfer_amount_avail=available,
            limit_time_period_days=self.time_period_days,
            token=transfer.token.name,
            message=message
        )

    def __init__(
            self,
            name: str,
            applied_to_transfer_types: AppliedToTypes,
            application_filter: ApplicationFilter,
            time_period_days: int,
            aggregation_filter: AggregationFilter = matching_transfer_type_filter,
            minimum_sent_aggregation_filter: AggregationFilter = regular_payment_filter
    ):
        super().__init__(
            name,
            applied_to_transfer_types,
            application_filter,
            time_period_days,
            aggregation_filter
        )

        self.minimum_sent_aggregation_filter = minimum_sent_aggregation_filter


class BalanceFractionLimit(AggregateTransferAmountMixin, AggregateLimit):
    """
    A limit that where the maximum amount that can be transferred over a set of transfers within a time period must be
    less than some fraction of the balance of the user at that moment.
    Has slightly weird behaviour in that the limit can change a lot depending on the balance (that's the point)
    Eg "Your current balance is 500 FooTokens, you're allowed to withdrawl half in a 7 day period.
    """

    def available_base(self, transfer: CreditTransfer) -> TransferAmount:
        return self._total_from_balance(transfer.sender_transfer_account.balance)

    def _total_from_balance(self, balance: int) -> TransferAmount:
        amount: TransferAmount = int(self.balance_fraction * balance)
        return amount

    def throw_validation_error(self, transfer: CreditTransfer, available: AggregateAvailability):
        message = 'Account % Limit "{}" reached. {} available'.format(
            self.name,
            max([available, 0])
        )
        raise TransferBalanceFractionLimitError(
            transfer_balance_fraction_limit=self.balance_fraction,
            transfer_amount_avail=available,
            limit_time_period_days=self.time_period_days,
            token=transfer.token.name,
            message=message
        )

    def __init__(
            self,
            name: str,
            applied_to_transfer_types: AppliedToTypes,
            application_filter: ApplicationFilter,
            time_period_days: int,
            balance_fraction: float,
            aggregation_filter: AggregationFilter = matching_transfer_type_filter

    ):
        super().__init__(
            name,
            applied_to_transfer_types,
            application_filter,
            time_period_days,
            aggregation_filter
        )

        self.balance_fraction = balance_fraction


class TransferCountLimit(AggregateLimit):
    """
    An Aggregate Limit that limits based on the number of transfers made (rather than the amount transferred).
    If we end up making more variations of this, it may be worth creating a count mixin.
    """

    def used_aggregator(
            self,
            transfer: CreditTransfer,
            query_constructor: QueryConstructorFunc
    ) -> AggregateAvailability:

        return query_constructor(
            transfer,
            db.session.query(func.count(CreditTransfer.id).label('count'))
        ).execution_options(show_all=True).first().count - 1

    def case_will_use(self, transfer: CreditTransfer) -> int:
        return 1

    def available_base(self, transfer: CreditTransfer) -> int:
        return self.transfer_count

    def throw_validation_error(self, transfer: CreditTransfer, available: AggregateAvailability):
        message = 'Account Limit "{}" reached. Allowed {} transaction per {} days' \
            .format(self.name, self.transfer_count, self.time_period_days)
        raise TransferCountLimitError(
            transfer_count_limit=self.transfer_count,
            limit_time_period_days=self.time_period_days,
            token=transfer.token.name,
            message=message
        )

    def __init__(
            self,
            name: str,
            applied_to_transfer_types: AppliedToTypes,
            application_filter: ApplicationFilter,
            time_period_days: int,
            transfer_count: int,
            aggregation_filter: AggregationFilter = matching_transfer_type_filter
    ):
        super().__init__(
            name,
            applied_to_transfer_types,
            application_filter,
            time_period_days,
            aggregation_filter
        )

        self.transfer_count = transfer_count


LIMITS = [
    TotalAmountLimit('Sempo Level 0: P7', GENERAL_PAYMENTS,
                     is_any_token_and_user_is_not_phone_and_not_kyc_verified, 7,
                     total_amount=config.TRANSFER_LIMITS['0.P7']),
    TotalAmountLimit('Sempo Level 0: P30', GENERAL_PAYMENTS,
                     is_any_token_and_user_is_not_phone_and_not_kyc_verified, 30,
                     total_amount=config.TRANSFER_LIMITS['0.P30']),

    NoTransferAllowedLimit('Sempo Level 0: WD30', [WITHDRAWAL, DEPOSIT],
                           is_any_token_and_user_is_not_phone_and_not_kyc_verified),

    TotalAmountLimit('Sempo Level 1: P7', GENERAL_PAYMENTS,
                     is_any_token_and_user_is_phone_but_not_kyc_verified, 7,
                     total_amount=config.TRANSFER_LIMITS['1.P7']),
    TotalAmountLimit('Sempo Level 1: P30', GENERAL_PAYMENTS,
                     is_any_token_and_user_is_phone_but_not_kyc_verified, 30,
                     total_amount=config.TRANSFER_LIMITS['1.P30']),
    NoTransferAllowedLimit('Sempo Level 1: WD30', [WITHDRAWAL, DEPOSIT],
                           is_any_token_and_user_is_phone_but_not_kyc_verified),

    TotalAmountLimit('Sempo Level 2: P7', GENERAL_PAYMENTS,
                     is_any_token_and_user_is_kyc_verified, 7,
                     total_amount=config.TRANSFER_LIMITS['2.P7']),
    TotalAmountLimit('Sempo Level 2: P30', GENERAL_PAYMENTS,
                     is_any_token_and_user_is_kyc_verified, 30,
                     total_amount=config.TRANSFER_LIMITS['2.P30']),
    TotalAmountLimit('Sempo Level 2: WD7', [WITHDRAWAL, DEPOSIT],
                     is_any_token_and_user_is_kyc_verified, 7,
                     total_amount=config.TRANSFER_LIMITS['2.WD7']),
    TotalAmountLimit('Sempo Level 2: WD30', [WITHDRAWAL, DEPOSIT],
                     is_any_token_and_user_is_kyc_verified, 30,
                     total_amount=config.TRANSFER_LIMITS['2.WD30']),

    TotalAmountLimit('Sempo Level 3: P7', GENERAL_PAYMENTS,
                     is_any_token_and_user_is_kyc_business_verified, 7,
                     total_amount=config.TRANSFER_LIMITS['3.P7']),
    TotalAmountLimit('Sempo Level 3: P30', GENERAL_PAYMENTS,
                     is_any_token_and_user_is_kyc_business_verified, 30,
                     total_amount=config.TRANSFER_LIMITS['3.P30']),
    TotalAmountLimit('Sempo Level 3: WD7', [WITHDRAWAL, DEPOSIT],
                     is_any_token_and_user_is_kyc_business_verified, 7,
                     total_amount=config.TRANSFER_LIMITS['3.WD7']),
    TotalAmountLimit('Sempo Level 3: WD30', [WITHDRAWAL, DEPOSIT],
                     is_any_token_and_user_is_kyc_business_verified, 30,
                     total_amount=config.TRANSFER_LIMITS['3.WD30']),

    NoTransferAllowedLimit('GE Liquid Token - Standard User',
                           [AGENT_OUT_PAYMENT, WITHDRAWAL],
                           is_user_and_liquid_token),

    TransferCountLimit('GE Liquid Token - Group Account User',
                       [AGENT_OUT_PAYMENT, WITHDRAWAL],
                       is_group_and_liquid_token, 30,
                       aggregation_filter=withdrawal_or_agent_out_and_not_excluded_filter,
                       transfer_count=1),

    BalanceFractionLimit('GE Liquid Token - Group Account User',
                         [AGENT_OUT_PAYMENT, WITHDRAWAL],
                         is_group_and_liquid_token, 30,
                         aggregation_filter=withdrawal_or_agent_out_and_not_excluded_filter,
                         balance_fraction=0.50),

    MinimumSentLimit('GE Liquid Token - Group Account User',
                     [AGENT_OUT_PAYMENT, WITHDRAWAL],
                     is_group_and_liquid_token, 30,
                     aggregation_filter=withdrawal_or_agent_out_and_not_excluded_filter
                     ),

    MaximumAmountPerTransferLimit('GE Liquid Token - Group Account User',
                                  [AGENT_OUT_PAYMENT, WITHDRAWAL],
                                  is_group_and_liquid_token,
                                  maximum_amount=config.TRANSFER_LIMITS['LT.MaxAm'])
]


def get_transfer_limits(transfer: CreditTransfer) -> List[BaseTransferLimit]:
    return [limit for limit in LIMITS if limit.applies_to_transfer(transfer)]

