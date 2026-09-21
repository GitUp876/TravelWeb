from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Departure


@pytest.mark.django_db
def test_home_lists_bookable_departures(client, departure):
    response = client.get(reverse("catalog:home"))
    assert response.status_code == 200
    assert departure.trip.title in response.content.decode()


@pytest.mark.django_db
def test_home_hides_unpublished_trips(client, departure):
    departure.trip.is_published = False
    departure.trip.save()
    response = client.get(reverse("catalog:home"))
    assert departure.trip.title not in response.content.decode()


@pytest.mark.django_db
def test_category_page_lists_its_trips(client, departure):
    url = reverse("catalog:category", kwargs={"category": departure.trip.category})
    response = client.get(url)
    assert response.status_code == 200
    assert departure.trip.title in response.content.decode()


@pytest.mark.django_db
def test_unknown_category_is_not_found(client, db):
    assert client.get("/trips/not-a-category/").status_code == 404


@pytest.mark.django_db
def test_trip_page_shows_its_dates(client, departure, price_option):
    response = client.get(departure.trip.get_absolute_url())
    assert response.status_code == 200
    body = response.content.decode()
    assert departure.start_date.strftime("%b") in body
    assert "149.00" in body


@pytest.mark.django_db
def test_unpublished_trip_page_is_not_found(client, trip):
    trip.is_published = False
    trip.save()
    assert client.get(trip.get_absolute_url()).status_code == 404


@pytest.mark.django_db
def test_departure_page_shows_prices_and_pickups(client, departure, price_option, pickup):
    response = client.get(departure.get_absolute_url())
    assert response.status_code == 200
    body = response.content.decode()
    assert "Adult" in body
    assert "Manchester Park &amp; Ride" in body


@pytest.mark.django_db
def test_draft_departure_page_is_not_found(client, departure):
    departure.status = Departure.Status.DRAFT
    departure.save()
    assert client.get(departure.get_absolute_url()).status_code == 404


@pytest.mark.django_db
def test_departure_page_advertises_a_payment_plan(client, departure, price_option):
    body = client.get(departure.get_absolute_url()).content.decode()
    assert "payment plan is available" in body


@pytest.mark.django_db
def test_departure_page_hides_the_plan_when_it_is_too_late(client, departure, price_option):
    departure.start_date = timezone.localdate() + timedelta(days=5)
    departure.end_date = departure.start_date
    departure.final_payment_due_date = departure.start_date
    departure.save()
    body = client.get(departure.get_absolute_url()).content.decode()
    assert "payment plan is available" not in body
