from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField
from wtforms.validators import DataRequired


class VersionCreatorForm(FlaskForm):
    version_number = StringField("Version", validators=[DataRequired()])
    candidate_number = StringField("Beta Number")
    branch_name = SelectField("Branch to build", coerce=str)

    taiga_flow = BooleanField("Taiga Flow", default=True)
    build_flow = BooleanField("Build Flow", default=True)
