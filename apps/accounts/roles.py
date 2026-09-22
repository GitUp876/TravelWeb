"""Role definitions.

Roles are Django permission groups, so they can be changed later without a
code deploy. The split is the one agreed in the build plan:

  Staff    bookings and traveller details, but not money or pricing
  Manager  everything Staff can do, plus pricing, publishing and refunds
  Owner    superuser; the only role that can create accounts
"""

STAFF = "Staff"
MANAGER = "Manager"

# (app_label, model, [permission codename suffixes])
STAFF_PERMISSIONS = [
    ("catalog", "trip", ["view"]),
    ("catalog", "departure", ["view"]),
    ("catalog", "pickuppoint", ["view"]),
    ("catalog", "departurepickup", ["view"]),
    ("catalog", "priceoption", ["view"]),
    ("bookings", "guest", ["add", "change", "view"]),
    ("bookings", "booking", ["add", "change", "view"]),
    ("bookings", "traveller", ["add", "change", "delete", "view"]),
    ("bookings", "waitlistentry", ["add", "change", "delete", "view"]),
    ("bookings", "payment", ["view"]),
    ("bookings", "paymentplan", ["view"]),
    ("bookings", "scheduledpayment", ["view"]),
]

MANAGER_PERMISSIONS = STAFF_PERMISSIONS + [
    ("catalog", "trip", ["add", "change", "delete"]),
    ("catalog", "departure", ["add", "change", "delete"]),
    ("catalog", "pickuppoint", ["add", "change", "delete"]),
    ("catalog", "departurepickup", ["add", "change", "delete"]),
    ("catalog", "priceoption", ["add", "change", "delete"]),
    ("catalog", "tripimage", ["add", "change", "delete", "view"]),
    ("catalog", "siteimage", ["add", "change", "delete", "view"]),
    ("catalog", "itineraryday", ["add", "change", "delete", "view"]),
    ("bookings", "booking", ["delete"]),
    ("bookings", "payment", ["add", "change"]),
    ("bookings", "paymentplan", ["add", "change"]),
    ("bookings", "scheduledpayment", ["add", "change"]),
    ("core", "auditevent", ["view"]),
]

ROLES = {STAFF: STAFF_PERMISSIONS, MANAGER: MANAGER_PERMISSIONS}
