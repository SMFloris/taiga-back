from django.db import migrations, models
import taiga.base.db.models.fields


class Migration(migrations.Migration):
    dependencies = [("projects", "0068_project_archived_code")]

    operations = [
        migrations.AddField(
            model_name=model,
            name="is_custom_links_activated",
            field=models.BooleanField(default=False, verbose_name="active custom links"),
        )
        for model in ("project", "projecttemplate")
    ] + [
        migrations.AddField(
            model_name=model,
            name="custom_links",
            field=taiga.base.db.models.fields.JSONField(blank=True, default=list, verbose_name="custom links"),
        )
        for model in ("project", "projecttemplate")
    ]
