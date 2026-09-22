from decimal import Decimal

import pytest

from apps.bookings.models import Traveller
from apps.catalog.models import PriceOption
from apps.payments.checkout import build_line_items, to_minor_units


def test_dollars_become_cents():
    assert to_minor_units(Decimal("149.00")) == 14900
    assert to_minor_units(Decimal("0.99")) == 99
    assert to_minor_units(Decimal("2395.50")) == 239550


@pytest.mark.django_db
def test_line_items_group_travellers_by_price_option(make_booking, departure, price_option):
    booking = make_booking(travellers=2)
    single = PriceOption.objects.create(
        departure=departure, label="Single occupancy", amount=Decimal("199.00")
    )
    Traveller.objects.create(booking=booking, full_name="Ari", price_option=single)

    items = {item["price_data"]["product_data"]["name"]: item for item in build_line_items(booking)}

    assert len(items) == 2
    adult = items["Newport Mansions — Adult"]
    assert adult["quantity"] == 2
    assert adult["price_data"]["unit_amount"] == 14900
    assert items["Newport Mansions — Single occupancy"]["quantity"] == 1


@pytest.mark.django_db
def test_line_item_prices_come_from_the_departure(make_booking, price_option):
    booking = make_booking(travellers=1)
    price_option.amount = Decimal("175.00")
    price_option.save()

    item = build_line_items(booking)[0]
    assert item["price_data"]["unit_amount"] == 17500
