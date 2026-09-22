"""Guest-facing pages. Read-only in phase 1: browsing, not booking."""

from django.db.models import Count, Min, Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.views.generic import DetailView, TemplateView

from .models import Departure, PriceOption, Trip, TripCategory
from .presentation import CATEGORY_DETAILS


class HomeView(TemplateView):
    template_name = "catalog/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["featured"] = (
            Departure.objects.bookable()
            .select_related("trip")
            .prefetch_related("price_options")
            .order_by("start_date")[:6]
        )
        return context


def category_summaries(photos: dict | None = None) -> list[dict]:
    """Each category with how many published trips have upcoming dates.

    With ``photos`` (from ``site_photos``), each entry also carries the picture
    that stands for the category.
    """
    counts = {
        row["category"]: row["trip_count"]
        for row in Trip.published.filter(departures__in=Departure.objects.bookable())
        .values("category")
        .annotate(trip_count=Count("id", distinct=True))
    }
    return [
        {
            "value": value,
            "label": label,
            "trip_count": counts.get(value, 0),
            **CATEGORY_DETAILS[value],
            **({"photo": photos[value]} if photos else {}),
        }
        for value, label in TripCategory.choices
    ]


class CategoryView(TemplateView):
    template_name = "catalog/category.html"

    def get_context_data(self, **kwargs):
        category = kwargs["category"]
        if category not in TripCategory.values:
            raise Http404("No such category")
        context = super().get_context_data(**kwargs)
        bookable = Departure.objects.bookable()
        context["category_label"] = TripCategory(category).label
        context["category_value"] = category
        context["category_blurb"] = CATEGORY_DETAILS[category]["blurb"]
        context["trips"] = (
            Trip.published.filter(category=category, departures__in=bookable)
            .annotate(
                next_departure=Min("departures__start_date", filter=Q(departures__in=bookable)),
                lead_price=Min(
                    "departures__price_options__amount",
                    filter=Q(departures__in=bookable, departures__price_options__is_available=True),
                ),
                departure_count=Count(
                    "departures", filter=Q(departures__in=bookable), distinct=True
                ),
            )
            .order_by("next_departure")
            .distinct()
        )
        return context


class TripDetailView(DetailView):
    template_name = "catalog/trip_detail.html"
    context_object_name = "trip"

    def get_queryset(self):
        return Trip.published.prefetch_related("itinerary_days", "images")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["departures"] = (
            self.object.departures.bookable()
            .prefetch_related(
                Prefetch(
                    "price_options",
                    queryset=PriceOption.objects.filter(is_available=True),
                ),
                "pickups__pickup_point",
            )
            .order_by("start_date")
        )
        return context


class DepartureDetailView(DetailView):
    template_name = "catalog/departure_detail.html"
    context_object_name = "departure"

    def get_object(self, queryset=None):
        departure = get_object_or_404(
            Departure.objects.select_related("trip").prefetch_related(
                "price_options", "pickups__pickup_point"
            ),
            pk=self.kwargs["pk"],
        )
        if not departure.trip.is_published or departure.status == Departure.Status.DRAFT:
            raise Http404("No such departure")
        return departure
