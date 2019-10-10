from flask import Blueprint, request, make_response
from flask.views import MethodView

from server.models.user import User
from server.models.ussd import UssdMenu
from server.utils.phone import proccess_phone_number
from server.utils.ussd.kenya_ussd import KenyaUssdProcessor
from server.utils.ussd.ussd import menu_display_text_in_lang, create_or_update_session

ussd_blueprint = Blueprint('ussd', __name__)


class ProcessKenyaUssd(MethodView):
    """
        USSD Entry method
        Method: POST

        @params sessionId - USSD session ID
        @params phoneNumber - MSISDN initiating the USSD request
        @params serviceCode - USSD code dialled to access the service eg. *701#
        @params text - User input. The initial input is blank

        Method returns a text response to be displayed to the user's screen
        All responses mid-session begin with CON. All responses that terminate the session begin with the word END
    """
    def post(self):
        post_data = request.get_json()

        session_id = post_data.get('sessionId')
        phone_number = post_data.get('phoneNumber')
        user_input = post_data.get('text')
        service_code = post_data.get('serviceCode')

        if phone_number:
            msisdn = proccess_phone_number(phone_number, 'KE')
            user = User.query.filter_by(phone=msisdn).first()
            # TODO(ussd): 'exit_not_registered' if no user
            if None in [user, msisdn, session_id]:
                current_menu = UssdMenu.find_by_name('exit_invalid_request')
                text = menu_display_text_in_lang(current_menu, user)
            else:
                current_menu = KenyaUssdProcessor.process_request(session_id, user_input, user)
                ussd_session = create_or_update_session(session_id, user, current_menu, user_input, service_code)
                text = menu_display_text_in_lang(current_menu, user)
                text = KenyaUssdProcessor.replace_vars(current_menu, ussd_session, text, user)
        else:
            current_menu = UssdMenu.find_by_name('exit_invalid_request')
            text = menu_display_text_in_lang(current_menu, None)

        return make_response(text, 200)


ussd_blueprint.add_url_rule(
    '/v1/ussd/kenya',
    view_func=ProcessKenyaUssd.as_view('ussd_kenya__view'),
    methods=['POST']
)
