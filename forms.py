from flask_wtf import FlaskForm
from wtforms import IntegerField, SelectField, StringField
from wtforms.validators import DataRequired


class VersionCreatorForm(FlaskForm):
    version_number = StringField("Version", validators=[DataRequired()])
    candidate_number = IntegerField("Beta Number")
    branch_name = SelectField("Branch to build", coerce=str)
