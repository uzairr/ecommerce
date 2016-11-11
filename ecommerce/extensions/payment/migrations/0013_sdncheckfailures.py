# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import jsonfield.fields


class Migration(migrations.Migration):

    dependencies = [
        ('basket', '0007_auto_20160907_2040'),
        ('payment', '0012_auto_20161109_1456'),
    ]

    operations = [
        migrations.CreateModel(
            name='SDNCheckFailures',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('full_name', models.CharField(max_length=255)),
                ('sdn_check_response', jsonfield.fields.JSONField()),
                ('failure_type', models.CharField(default=b'Matched', max_length=255, choices=[(b'Matched', 'SDN check match'), (b'Connection Error', 'Could not connect to SDN API')])),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('basket', models.ForeignKey(to='basket.Basket')),
            ],
            options={
                'verbose_name': 'SDN Check Failure',
            },
        ),
    ]
