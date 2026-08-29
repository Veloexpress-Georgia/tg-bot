# Veloexpress

The shared language for planning shuttle lifts, holding seats, and settling the money for them.

## Language

**Lift**:
A shuttle departure at one time on one service day.
_Avoid_: Slot, trip

**Booking**:
A rider's current intention to occupy a seat on a lift. A booking may move or disappear as the rider changes their poll answer.
_Avoid_: Reservation, payment

**Seat**:
One place on one lift, held by a rider, their guest, or an offline booking.
_Avoid_: Person, ride

**Payment**:
An immutable fact that money was reported as received for a rider on a service day.
_Avoid_: Claim, paid flag

**Coverage**:
The current assignment of a rider's paid seat-units to their bookings. Coverage may move when bookings change; the underlying payment does not.
_Avoid_: Payment allocation, paid booking

**Amount due**:
The difference between the price of a rider's chargeable seats and the money already received for the service day.
_Avoid_: Balance

**Deadline roster**:
The immutable record of seats and coverage at the booking deadline.
_Avoid_: Live roster, current bookings

**Service day defaults**:
The runtime-editable price and deadline copied into a service day when it is first published. Changing defaults never changes an existing service day.
_Avoid_: Current terms, global price

**Funded lift**:
A lift whose minimum required seats are covered by received payments or offline bookings.
_Avoid_: Running lift, full lift
