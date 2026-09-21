from django.urls import path

from . import views

app_name = "bookings"

urlpatterns = [
    path("departure/<int:pk>/book/", views.book_departure, name="book"),
    path("booking/find/", views.find_booking, name="find"),
    path("booking/<str:token>/", views.booking_detail, name="detail"),
]
