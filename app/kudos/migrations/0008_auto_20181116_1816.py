# Generated by Django 2.1.2 on 2018-11-16 18:16

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('kudos', '0007_merge_20181114_2349'),
    ]

    operations = [
        migrations.AddField(
            model_name='kudostransfer',
            name='primary_email',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
