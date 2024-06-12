from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, DateField
from wtforms.validators import DataRequired


class VersionCreatorForm(FlaskForm):
    version_number = StringField("Version", validators=[DataRequired()])
    candidate_number = StringField("Beta Number")

    branch_name = SelectField("Branch to build", coerce=str)
    commit_sha = StringField("Commit to build (optional)")
    date = DateField("Only include files after (optional)")

    taiga_flow = BooleanField("Taiga Flow", default=True)
    build_flow = BooleanField("Build Flow", default=True)
