define([
        'jquery',
        'jquery-ajax-retry',
        'backbone',
        'underscore',
        'currency-symbol',
        'edx-ui-toolkit/utils/string-utils',
        'utils/analytics_utils',
        'js-cookie',
        'date-utils',
        'bootstrap',
        'jquery-url'
    ],
function ($, AjaxRetry, Backbone, _, Currency, StringUtils, AnalyticsUtils, Cookies) {
    'use strict';

    return Backbone.View.extend({
        orderId: null,
        el: '#receipt-container',

        events: {
          'click #credit-button': 'getCredit'
        },

        initialize: function () {
            this.orderId = this.orderId || $.url('?order_number');
            this.renderReceipt();
        },

        renderReceipt: function () {
            // After fully rendering the template, attach analytics click handlers
            AnalyticsUtils.instrumentClickEvents();
            // Fire analytics event that order has completed
            this.trackPurchase();
            return this;
        },

        trackPurchase: function() {
            $.ajax({
                url: '/api/v2/orders/' + this.orderId,
                method: 'GET',
                headers: {
                    'X-CSRFToken': Cookies.get('ecommerce_csrftoken')
                },
                success: function(data) {
                    AnalyticsUtils.trackingModel.trigger('segment:track', 'Completed Purchase', {
                        orderId: data.number,
                        total: data.total_excl_tax,
                        currency: data.currency
                    });
                }
            });
        },

        render: function () {
            var self = this;

            if (this.orderId) {
                // Get the order details
                self.$el.removeClass('hidden');
            } else {
                this.renderError();
            }
        },

        /**
         * Completes the process of getting credit for the course.
         *
         */
        getCredit: function (event) {     // jshint ignore:line
            try {
                event.preventDefault();
            } catch (err) {
                // Ignore the error as not all event inputs have the preventDefault method.
            }
            var creditButton = $('#credit-button'),
                courseKey = creditButton.data('course-key'),
                username = creditButton.data('username'),
                providerId = creditButton.data('provider'),
                $errorContainer = $('#error-container');
            /* jshint unused:vars */
            $.ajax({
                url: StringUtils.interpolate(
                    '{lms_url}/api/credit/v1/providers/{providerId}/request/',
                    {lms_url: $('#receipt-container').data('lms-url'), providerId: providerId}
                ),
                type: 'POST',
                headers: {
                    'X-CSRFToken': Cookies.get('ecommerce_csrftoken')
                },
                dataType: 'json',
                contentType: 'application/json',
                data: JSON.stringify({
                    'course_key': courseKey,
                    'username': username
                }),
                context: this,
                success: function (requestData) {
                    var $form = $('<form>', {
                        'class': 'hidden',
                        'action': requestData.url,
                        'method': 'POST',
                        'accept-method': 'UTF-8'
                    });

                    _.each(requestData.parameters, function (value, key) {
                        $('<textarea>').attr({
                            name: key,
                            value: value
                        }).appendTo($form);
                    });

                    $form.appendTo('body').submit();
                },
                error: function (xhr, ajaxOptions, thrownError) {
                    $errorContainer.removeClass('hidden');
                }
            });
        }
    });
});     // jshint ignore:line
