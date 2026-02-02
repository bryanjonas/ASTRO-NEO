# Transaction Semantics Fix

## Problem

The capture flow in `sequential_capture.py` had inconsistent transaction semantics that could leave the database in an inconsistent state:

1. **Capture record created and committed immediately** (line 561, old)
2. **Intermediate commits** after updating path (line 626, old)
3. **If solve failed mid-way**, capture record existed with incomplete data
4. **Result**: Orphaned capture records with `has_wcs=False` and no useful data

## Root Cause

The service committed the `CaptureLog` record before the critical operations (solve, association) completed. This violated atomicity - the capture record should only be committed when it's in a consistent state (either fully successful or marked as failed with an error message).

## Solution

Implemented proper transaction boundaries using `db.flush()` and deferred commits:

### Key Changes

1. **Use `db.flush()` instead of `db.commit()`** after creating capture record (line 563)
   - Writes to database and gets auto-generated ID
   - Does NOT commit the transaction
   - Allows rollback if system crashes

2. **Remove intermediate commits** during capture/solve flow
   - Path update no longer commits (line 627)
   - Solve success no longer commits (line 701)
   - Transaction remains open until final state is known

3. **Commit only at final state**:
   - **Capture failed**: Commit with `error_message` set (lines 584, 614, 709)
   - **Solve succeeded**: Commit after association completes (lines 744, 766, 782)
   - **Association failed**: Still commit the solved capture (line 782)

### Transaction Flow

```
BEGIN TRANSACTION
  ├─ Create CaptureLog (flush to get ID)
  ├─ Take exposure
  ├─ Update path
  ├─ Solve FITS
  │   ├─ SUCCESS: Continue to association
  │   └─ FAILURE: Set error_message, COMMIT, return
  ├─ Associate sources
  │   ├─ SUCCESS: COMMIT with association
  │   ├─ NO MATCH: COMMIT without association (still valid)
  │   └─ FAILURE: COMMIT without association (still valid)
END TRANSACTION
```

### Error Recovery

- **Capture failure**: Record committed with `error_message`, no FITS path
- **FITS not found**: Record committed with `error_message`, no FITS path
- **Solve failure**: Record committed with `has_wcs=False` and `error_message`
- **Association failure**: Record committed with solve data but no association
- **System crash during solve**: Transaction rolls back, no orphaned record

## Benefits

1. **Atomicity**: Capture records are only committed in consistent states
2. **Crash recovery**: System crashes don't leave orphaned partial records
3. **Debugging**: Failed captures are still tracked with error messages
4. **Consistency**: Database state always reflects actual operation outcome

## Testing Recommendations

1. Test solve timeout/failure scenarios
2. Test system crash during solve (kill process)
3. Verify failed captures are logged correctly
4. Verify successful captures have all fields populated
5. Test rollback behavior with `db.rollback()` calls

## Files Modified

- `app/services/sequential_capture.py`: Transaction management for capture flow

## Related Issues

This fixes Critical Issue #3 from the code review: "No transaction semantics - If a solve fails mid-capture, the database is left in an inconsistent state."
