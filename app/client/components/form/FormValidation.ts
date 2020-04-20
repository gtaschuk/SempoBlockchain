import * as Sentry from "@sentry/browser";
import { parsePhoneNumberFromString } from "libphonenumber-js";
import store from "../../createStore.js";

const FormValidation = {
  required: (value: string | number) =>
    value !== undefined && value !== "" ? undefined : "This field is required",
  phone: (value: string) => {
    if (value) {
      let countryCode;
      let orgId;
      try {
        orgId = store.getState().login.organisationId;
        countryCode =
          orgId && store.getState().organisations.byId[orgId].country_code;
      } catch (e) {
        console.log("Something went wrong", e);
        countryCode = undefined;
        Sentry.captureException(e);
      }

      const number = parsePhoneNumberFromString(value, countryCode);
      if (number && number.isValid()) {
        return undefined;
      } else {
        return "Please enter a proper phone number";
      }
    }
  },
  notOther: (value: string) =>
    value.toLowerCase() === "other"
      ? "'Other' is not a valid input"
      : undefined,
  isNumber: (value: number) => (isNaN(value) ? "Must be a number" : undefined)
};

export default FormValidation;
