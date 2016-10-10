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
            // this.trackPurchase();
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
        }
    });
});
