# Keep day payments immutable and lift coverage movable

Payments are append-only facts for a rider and service day, while coverage is a replaceable projection over that rider's current bookings. Coverage stays on bookings that still exist and moves to new bookings when a rider changes lifts before the deadline; adding more seats creates an amount due. At the deadline the covered roster is frozen: already covered seats are spent, while later additional seats require additional payment. This keeps financial history honest without preventing the poll changes that are normal for the group.
