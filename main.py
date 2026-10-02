#!/usr/bin/env python3
"""
Simple command‑line calculator that supports +, -, *, and /.
"""

import sys

def add(a, b):
    """Return the sum of a and b."""
    return a + b

def subtract(a, b):
    """Return the difference of a and b."""
    return a - b

def multiply(a, b):
    """Return the product of a and b."""
    return a * b

def divide(a, b):
    """Return the quotient of a and b. Raises ZeroDivisionError if b is zero."""
    if b == 0:
        raise ZeroDivisionError("Cannot divide by zero.")
    return a / b

def get_number(prompt):
    """Prompt the user for a number and return it as a float."""
    while True:
        try:
            return float(input(prompt))
        except ValueError:
            print("Invalid input. Please enter a numeric value.")

def get_operation():
    """Prompt the user for an operation and return it."""
    ops = {'+': add, '-': subtract, '*': multiply, '/': divide}
    while True:
        op = input("Enter operation (+, -, *, /): ").strip()
        if op in ops:
            return op, ops[op]
        else:
            print("Unsupported operation. Please choose one of +, -, *, /.")

def main():
    """Main calculator loop."""
    print("=== Simple Python Calculator ===")
    while True:
        num1 = get_number("Enter the first number: ")
        op_symbol, operation = get_operation()
        num2 = get_number("Enter the second number: ")

        try:
            result = operation(num1, num2)
        except ZeroDivisionError as e:
            print(f"Error: {e}")
            continue

        print(f"Result: {num1} {op_symbol} {num2} = {result}")

        again = input("Perform another calculation? (y/n): ").strip().lower()
        if again not in ('y', 'yes'):
            print("Goodbye!")
            sys.exit(0)

if __name__ == "__main__":
    main()