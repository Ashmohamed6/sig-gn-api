from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_user_created_at_user_updated_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("reader", "Lecteur"),
                    ("editor", "\u00c9diteur / Analyste"),
                    ("manager", "Chef d'\u00e9quipe (Admin niveau 1)"),
                    ("project_manager", "Chef de projet (Admin niveau 2)"),
                    ("admin", "Admin"),
                ],
                default="reader",
                max_length=20,
            ),
        ),
    ]
