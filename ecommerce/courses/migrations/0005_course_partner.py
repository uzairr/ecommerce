# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('partner', '0009_partner_organization'),
        ('courses', '0004_auto_20150803_1406'),
    ]

    operations = [
        migrations.AddField(
            model_name='course',
            name='partners',
            field=models.ManyToManyField(to='partner.Partner'),
        ),
    ]
