"""Deterministic attribute and invoice rules prescribed by the assignment."""

from hashlib import md5


GENDERS = ("Male", "Female")
AGE_GROUPS = ("0–17", "18–25", "26–35", "36–45", "46–50", "51–55", "55+")
OCCUPATIONS = (
    "Student", "Engineer", "Teacher", "Executive", "Clerk",
    "Healthcare", "Retail", "Homemaker", "Artist", "Other",
)
CITY_CATEGORIES = ("A", "B", "C")
STAY_YEARS = ("0", "1", "2", "3", "4+")
MARITAL_STATUSES = ("Single", "Married")
PRODUCT_CATEGORIES = (
    "Home Decor", "Kitchen", "Stationery", "Toys", "Seasonal Gifts",
    "Bags", "Jewelry", "Garden", "Party", "Other",
)
SUPPLIERS = (
    ("SUP01", "Northwind Supplies"),
    ("SUP02", "Atlantic Gifts Ltd"),
    ("SUP03", "Midlands Wholesale"),
    ("SUP04", "Celtic Craft Co"),
    ("SUP05", "EuroPack Partners"),
)
STORE_LIST = {
    1: ("Online", "Web"),
    2: ("London Bridge", "London"),
    3: ("Manchester Arndale", "Manchester"),
    4: ("Birmingham Bullring", "Birmingham"),
    5: ("Edinburgh Princes", "Edinburgh"),
}


def stable(text, n):
    return int(md5(text.encode("utf-8")).hexdigest()[:8], 16) % n


def sales_assignment(transaction_id):
    number = int(md5(transaction_id.encode("utf-8")).hexdigest()[:8], 16)
    if number % 10 <= 6:
        return "Online", 1
    return "Periodic", 2 + number % 4


def customer_attributes(customer_id, first_country):
    return (
        customer_id,
        GENDERS[stable(customer_id, 2)],
        AGE_GROUPS[stable(customer_id + "age", 7)],
        OCCUPATIONS[stable(customer_id + "occ", 10)],
        CITY_CATEGORIES[stable(customer_id + first_country, 3)],
        STAY_YEARS[stable(customer_id + "stay", 5)],
        MARITAL_STATUSES[stable(customer_id + "mar", 2)],
    )


def product_attributes(product_id, longest_description):
    supplier_id, supplier_name = SUPPLIERS[stable(product_id, 5)]
    return (
        product_id, longest_description or "UNKNOWN",
        PRODUCT_CATEGORIES[stable(product_id + "cat", 10)],
        supplier_id, supplier_name,
    )
