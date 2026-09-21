from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("trips/<slug:category>/", views.CategoryView.as_view(), name="category"),
    path("trip/<slug:slug>/", views.TripDetailView.as_view(), name="trip-detail"),
    path("departure/<int:pk>/", views.DepartureDetailView.as_view(), name="departure-detail"),
]
