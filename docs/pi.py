import time
from decimal import Decimal, getcontext

# ------------------------------------------------------------
# Chudnovsky implementation
# ------------------------------------------------------------
def pi_chudnovsky(digits: int = 1000) -> str:
    """
    Return π rounded to *digits* decimal places.
    Uses the Chudnovsky series:
        1/π = 12 * Σ_{k=0}∞ ((-1)^k * (6k)! / ((k!)^3 (3k)!)) *
                 (1/640320^3)^k * (13591409 + 545140134k)

    The series is rearranged as:
        π = (426880 * sqrt(10005)) /
            Σ_{k=0}∞ ((-1)^k * (6k)! * (13591409 + 545140134k)) /
                     ((3k)! (k!)^3 (640320^(3k)))

    Parameters
    ----------
    digits : int
        Number of decimal places of π to compute (default 1000).

    Returns
    -------
    str
        π as a decimal string with the requested number of places.
    """

    # 1.  Set context precision (a few extra digits for intermediate rounding)
    getcontext().prec = digits + 10

    # 2.  Constants
    C = 426880 * Decimal(10005).sqrt()  # 426880 * sqrt(10005)

    # 3.  Initial values for the series (k = 0)
    M = 1              # M_k = (6k)!/(3k)!/(k!)^3  at k=0 -> 1
    L = 13591409       # L_k = 13591409 + 545140134k  at k=0 -> 13591409
    X = 1              # X_k = (-262537412640768000)^k
    K = 6              # K = 6 + 12k
    S = Decimal(L)     # Series sum starts with k=0 term

    # 4.  How many terms do we need?
    # Each term adds ~14.181647462725477 digits.  A few extra terms for safety.
    terms_needed = int(digits / 14.181647462725477) + 2

    # 5.  Iterate the series
    for i in range(1, terms_needed):
        # M_{k} = M_{k-1} * ( (K^3 - 16K) / i^3 )
        M = (M * (K**3 - 16*K)) // (i**3)

        # L_{k} = L_{k-1} + 545140134
        L += 545140134

        # X_{k} = X_{k-1} * (-262537412640768000)
        X *= -262537412640768000

        # Add the term to the sum:  M*L / X
        S += Decimal(M * L) / Decimal(X)

        # Prepare for next iteration: K increases by 12
        K += 12

    # 6.  Compute π and round to the requested precision
    pi = C / S
    # The format string rounds to the requested number of decimal places.
    return format(pi, f'.{digits}f')

# ------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------
if __name__ == "__main__":
    requested_digits = 10000

    start = time.time()
    pi_str = pi_chudnovsky(requested_digits)
    elapsed = time.time() - start

    # Print π with the requested number of decimal places
    print(pi_str)

    # Optionally, print how long it took
    print(f"\nComputed pi to {requested_digits} digits in {elapsed:.4f} seconds.")
