from django import template
from decimal import Decimal, InvalidOperation

register = template.Library()


@register.filter
def naira(value):
    try:
        amount = Decimal(str(value or 0))
        return f"₦{amount:,.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return "₦0.00"
