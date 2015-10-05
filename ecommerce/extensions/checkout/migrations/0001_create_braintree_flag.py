# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations

FLAG_NAME = 'pay-with-braintree'


def create_braintree_flag(apps, schema_editor):
    """ Create a Flag to control the usage of the Braintree payment processor. """
    apps.get_model('waffle', 'Flag').objects.create(name=FLAG_NAME)


def destroy_braintree_flag(apps, schema_editor):
    """ Destroy the Braintree Flag. """
    apps.get_model('waffle', 'Flag').objects.get(name=FLAG_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('waffle', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_braintree_flag, destroy_braintree_flag),
    ]
