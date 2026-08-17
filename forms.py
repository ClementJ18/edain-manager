from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField
from wtforms.validators import DataRequired


class VersionCreatorForm(FlaskForm):
    version_number = StringField("Version", validators=[DataRequired()])
    candidate_number = StringField("Beta Number")


class PatcherForm(FlaskForm):
    """The fixed half of the patcher page: everything that does not depend on pySAGE's registry.

    The patch checkboxes, their parameters and the upload field per target binary are all built
    from that registry at render time and read back out of `request.form` / `request.files`, so a
    patch (or a whole new target binary) appears on the page without this class changing. See
    `patching`.
    """

    # Neither agreement below is enforced here: the acknowledgement is only required when an
    # experimental patch is actually selected, which the view knows and the form does not.
    show_experimental = BooleanField("Show experimental patches")
    experimental_agreement = BooleanField(
        "I understand that experimental patches may crash, desync or break saves and replays, "
        "and I am keeping an unpatched copy of this file"
    )

    credit_agreement = BooleanField(
        "I will credit the patch authors listed above in my mod's credits",
        validators=[
            DataRequired(
                message="The patch authors have to be credited - tick the box to agree."
            )
        ],
    )
