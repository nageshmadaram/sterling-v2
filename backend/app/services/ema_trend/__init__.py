"""Tenant-isolated EMA trend observations and virtual execution.

This package never constructs a live broker write client. Market reads use the
owner's account; every order uses the existing simulated tenant transport.
"""
